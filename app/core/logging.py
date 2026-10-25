"""结构化日志配置（T0-3）。

基于 structlog 实现 JSON 格式结构化日志：
- 必含字段：timestamp/level/logger/message/trace_id/task_id
- Docker 环境仅输出 stdout（P2-10）
- APP_LOG_TO_FILE=true 时输出文件（按天轮转保留7天）
- API Key 等敏感字段脱敏

架构决策（P2-10）：Docker 环境下日志仅输出 stdout，
由 Docker 日志驱动收集；文件输出仅在非容器化部署时使用。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import structlog

from app.config import get_config

# 敏感字段正则匹配（API Key、Token、Secret 等）
_SENSITIVE_PATTERNS = [
    re.compile(r"(api[_-]?key|token|secret|password|authorization)\s*[:=]\s*[\"']?([^\"'\s,}]+)", re.IGNORECASE),
]

# 全局 trace_id 上下文（通过 contextvars 在请求间传递）
_trace_id: str = "unknown"
_task_id: str | None = None


def set_trace_id(trace_id: str) -> None:
    """设置当前请求的 trace_id（中间件调用）。

    Args:
        trace_id: 请求唯一标识
    """
    global _trace_id
    _trace_id = trace_id


def get_trace_id() -> str:
    """获取当前 trace_id。

    Returns:
        当前请求的 trace_id，默认为 "unknown"
    """
    return _trace_id


def set_task_id(task_id: str | None) -> None:
    """设置当前任务 ID（Worker 执行时调用）。

    Args:
        task_id: 任务 ID，为 None 时清除
    """
    global _task_id
    _task_id = task_id


def _sanitize_sensitive_data(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """structlog Processor：脱敏敏感字段。

    将日志中匹配到的 API Key/Token 等替换为 ***。
    """
    for key, value in list(event_dict.items()):
        if isinstance(value, str):
            for pattern in _SENSITIVE_PATTERNS:
                value = pattern.sub(lambda m: f"{m.group(1)}=***", value)
            event_dict[key] = value
    return event_dict


def _add_context(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """structlog Processor：注入 trace_id 和 task_id 上下文。"""
    event_dict["trace_id"] = _trace_id
    if _task_id:
        event_dict["task_id"] = _task_id
    return event_dict


class DailyFileHandler(logging.Handler):
    """按天轮转的文件日志处理器（保留7天）。

    仅在 APP_LOG_TO_FILE=true 时使用，Docker 环境不启用。
    """

    def __init__(self, log_dir: str, retention_days: int = 7) -> None:
        """初始化文件处理器。

        Args:
            log_dir: 日志目录路径
            retention_days: 保留天数，超过自动清理
        """
        super().__init__()
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.retention_days = retention_days
        self._current_date: str | None = None
        self._file_handler: logging.FileHandler | None = None

    def _get_log_path(self) -> Path:
        """获取当天日志文件路径。"""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.log_dir / f"app_{today}.log"

    def _rotate_if_needed(self) -> None:
        """检查日期变更，必要时切换文件并清理旧日志。"""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._current_date != today:
            if self._file_handler:
                self._file_handler.close()
            self._current_date = today
            self._file_handler = logging.FileHandler(self._get_log_path(), encoding="utf-8")
            self._cleanup_old_logs()

    def _cleanup_old_logs(self) -> None:
        """清理超过保留天数的旧日志文件。"""
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
        for log_file in self.log_dir.glob("app_*.log"):
            try:
                file_date = datetime.strptime(log_file.stem.replace("app_", ""), "%Y-%m-%d")
                file_date = file_date.replace(tzinfo=timezone.utc)
                if file_date < cutoff:
                    log_file.unlink()
            except (ValueError, OSError):
                continue

    def emit(self, record: logging.LogRecord) -> None:
        """写入日志记录。"""
        try:
            self._rotate_if_needed()
            if self._file_handler:
                self._file_handler.emit(record)
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        """关闭文件处理器。"""
        if self._file_handler:
            self._file_handler.close()
        super().close()


def setup_logging() -> None:
    """初始化全局日志配置。

    配置 structlog 处理器链：
    1. 添加上下文（trace_id/task_id）
    2. 脱敏敏感数据
    3. 格式化输出（JSON 或 控制台）

    注意：此函数应在应用启动时调用一次。
    """
    config = get_config()

    # 配置标准 logging（structlog 底层依赖）
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(message)s",
        handlers=[logging.StreamHandler()],
    )

    # 构建 structlog 处理器链
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_context,
        _sanitize_sensitive_data,
        structlog.processors.StackInfoRenderer(),
    ]

    if config.log_json_format:
        processors.append(structlog.processors.JSONRenderer(ensure_ascii=False))
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, config.log_level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # 文件输出（非 Docker 环境可选）
    if config.log_to_file:
        file_handler = DailyFileHandler(config.log_dir, config.log_retention_days)
        root_logger = logging.getLogger()
        root_logger.addHandler(file_handler)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """获取结构化日志记录器。

    Args:
        name: 日志器名称，通常传入 __name__

    Returns:
        structlog 绑定日志器
    """
    return structlog.get_logger(name)
