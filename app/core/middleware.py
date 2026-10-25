"""FastAPI 中间件（T0-5，P1）。

实现三个核心中间件：
1. TraceIDMiddleware：生成与透传 trace_id，注入响应头
2. APIKeyAuthMiddleware：API Key 鉴权（写操作强制，读操作开放，未配置则不鉴权）
3. RateLimitMiddleware：简单限流（可选，基于内存计数器）

架构决策（X-02）：基线排除多租户/用户权限体系，
采用轻量级 API Key 机制，仅对写操作强制鉴权。
"""

from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from typing import Any, Deque

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.config import get_config
from app.core.exceptions import AppException, ErrorCode
from app.core.logging import get_logger, set_trace_id

logger = get_logger(__name__)

# 写操作 HTTP 方法集合
_WRITE_METHODS = {"POST", "PUT", "DELETE", "PATCH"}

# 不需要鉴权的路径前缀（健康检查、文档等）
_PUBLIC_PATH_PREFIXES = ("/api/v1/health", "/docs", "/redoc", "/openapi.json", "/metrics")


class TraceIDMiddleware(BaseHTTPMiddleware):
    """Trace ID 生成与透传中间件。

    - 请求头 X-Trace-Id 存在时透传，否则生成新的 UUID
    - trace_id 注入日志上下文和响应头
    - 记录请求耗时
    """

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        """处理请求，生成/透传 trace_id。

        Args:
            request: FastAPI 请求对象
            call_next: 下一个中间件/路由处理函数

        Returns:
            带有 X-Trace-Id 响应头的 Response
        """
        trace_id = request.headers.get("X-Trace-Id") or str(uuid.uuid4())
        set_trace_id(trace_id)
        request.state.trace_id = trace_id

        start_time = time.perf_counter()
        response = None
        try:
            response = await call_next(request)
        finally:
            duration_ms = (time.perf_counter() - start_time) * 1000
            logger.info(
                "request_completed",
                method=request.method,
                path=request.url.path,
                status_code=getattr(response, "status_code", 500) if response else 500,
                duration_ms=round(duration_ms, 2),
            )

        if response is not None:
            response.headers["X-Trace-Id"] = trace_id
        return response


class APIKeyAuthMiddleware(BaseHTTPMiddleware):
    """API Key 鉴权中间件。

    规则（架构 §5.3）：
    - 环境变量 APP_API_KEY 未配置时，不鉴权（全部开放）
    - 配置后，写操作（POST/PUT/DELETE/PATCH）强制要求 X-API-Key 请求头
    - 读操作（GET/HEAD/OPTIONS）开放
    - 健康检查、文档等公共路径不鉴权
    """

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        """校验 API Key（写操作强制）。

        Args:
            request: FastAPI 请求对象
            call_next: 下一个处理函数

        Returns:
            Response 或 401 错误响应

        Raises:
            AppException: API Key 无效时抛出（由全局异常处理器捕获）
        """
        config = get_config()

        # 未配置 API Key 时不鉴权
        if not config.api_key:
            return await call_next(request)

        # 公共路径不鉴权
        if any(request.url.path.startswith(prefix) for prefix in _PUBLIC_PATH_PREFIXES):
            return await call_next(request)

        # 读操作开放
        if request.method not in _WRITE_METHODS:
            return await call_next(request)

        # 写操作强制鉴权
        api_key = request.headers.get("X-API-Key")
        if not api_key or api_key != config.api_key:
            logger.warning(
                "unauthorized_access",
                method=request.method,
                path=request.url.path,
                has_key=bool(api_key),
            )
            raise AppException(ErrorCode.UNAUTHORIZED, "无效或缺失的 API Key")

        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """简单内存限流中间件（可选启用）。

    基于客户端 IP 的滑动窗口限流，默认不启用。
    适用于单机部署，多实例需替换为 Redis 限流。

    配置项：APP_RATE_LIMIT_ENABLED=true 启用
    """

    def __init__(self, app: ASGIApp, max_requests: int = 100, window_seconds: int = 60) -> None:
        """初始化限流器。

        Args:
            app: ASGI 应用
            max_requests: 时间窗口内最大请求数
            window_seconds: 时间窗口大小（秒）
        """
        super().__init__(app)
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: dict[str, Deque[float]] = defaultdict(deque)

    def _is_rate_limited(self, client_ip: str) -> bool:
        """检查客户端 IP 是否超过限流阈值。

        Args:
            client_ip: 客户端 IP 地址

        Returns:
            True 表示被限流，False 表示允许通过
        """
        now = time.time()
        window = self._requests[client_ip]

        # 移除窗口外的旧请求记录
        while window and now - window[0] > self.window_seconds:
            window.popleft()

        if len(window) >= self.max_requests:
            return True

        window.append(now)
        return False

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        """执行限流检查。

        Args:
            request: FastAPI 请求对象
            call_next: 下一个处理函数

        Returns:
            Response 或 429 错误响应
        """
        client_ip = request.client.host if request.client else "unknown"

        if self._is_rate_limited(client_ip):
            logger.warning("rate_limited", client_ip=client_ip, path=request.url.path)
            raise AppException(ErrorCode.RATE_LIMITED, "请求过于频繁，请稍后重试")

        return await call_next(request)


def register_middlewares(app: Any) -> None:
    """向 FastAPI 应用注册全部中间件。

    中间件执行顺序（后进先出）：
    1. RateLimitMiddleware（最外层，最先执行）
    2. APIKeyAuthMiddleware
    3. TraceIDMiddleware（最内层，最后执行但最先处理响应）

    Args:
        app: FastAPI 应用实例
    """
    # TraceID 最内层（最后注册，最先处理响应）
    app.add_middleware(TraceIDMiddleware)

    # API Key 鉴权
    app.add_middleware(APIKeyAuthMiddleware)

    # 限流（可选，默认不启用）
    config = get_config()
    if getattr(config, "rate_limit_enabled", False):
        app.add_middleware(RateLimitMiddleware)
