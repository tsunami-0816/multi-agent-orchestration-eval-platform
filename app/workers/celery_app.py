"""Celery 应用配置（T5-1）。

配置 Celery 异步任务队列：
- Broker: Redis（生产）/ 内存（开发降级）
- Backend: Redis / SQLite
- 任务序列化：JSON
- 并发：prefork（Linux）/ solo（Windows 降级）

架构决策（T-01）：Celery 用于异步执行长耗时任务，
避免阻塞 API 响应。Windows 开发环境降级为 solo 或同步模式。
"""

from __future__ import annotations

import os
from typing import Any

from app.config import get_config
from app.core.logging import get_logger

logger = get_logger(__name__)

_config = get_config()

# Celery 配置
CELERY_CONFIG: dict[str, Any] = {
    "broker_url": getattr(_config, "celery_broker_url", "redis://localhost:6379/0"),
    "result_backend": getattr(_config, "celery_result_backend", "redis://localhost:6379/1"),
    "task_serializer": "json",
    "result_serializer": "json",
    "accept_content": ["json"],
    "timezone": "Asia/Shanghai",
    "enable_utc": True,
    "task_track_started": True,
    "task_time_limit": getattr(_config, "celery_task_time_limit", 600),  # 10分钟
    "task_soft_time_limit": getattr(_config, "celery_task_soft_time_limit", 540),
    "worker_prefetch_multiplier": 1,
    "worker_max_tasks_per_child": 100,
    "result_expires": 86400,  # 24小时
}

# 是否启用 Celery（可通过配置关闭，使用同步降级）
# 修复Bug-001 改动原因：默认值应为False，开发环境未启动Celery Worker时走同步降级，避免任务永久pending
CELERY_ENABLED = getattr(_config, "celery_enabled", False)

try:
    from celery import Celery

    celery_app = Celery("multi-agent-eval")
    celery_app.conf.update(**CELERY_CONFIG)

    # 自动发现任务
    celery_app.autodiscover_tasks(["app.workers.tasks"])

    logger.info("celery_initialized", broker=CELERY_CONFIG["broker_url"])

except ImportError:
    celery_app = None
    CELERY_ENABLED = False
    logger.warning("celery_not_installed, falling back to sync mode")


def get_celery_app():
    """获取 Celery 应用实例。

    Returns:
        Celery 实例，未安装时返回 None
    """
    return celery_app


def is_celery_enabled() -> bool:
    """检查 Celery 是否启用。

    Returns:
        True 表示 Celery 可用且已启用
    """
    return CELERY_ENABLED and celery_app is not None
