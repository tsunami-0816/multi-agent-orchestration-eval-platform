"""配置管理模块（T0-2）。

基于 Pydantic Settings v2 实现单一配置源：
- 基础配置从 config.yaml 加载
- 环境变量（APP_ 前缀）覆盖 YAML 配置
- 类型安全，mypy 通过

架构决策（P2-5）：不再维护 dev/test/prod 三套 YAML，
统一 config.yaml + 环境变量覆盖。
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _load_yaml_config() -> dict[str, Any]:
    """加载 config.yaml 配置文件。

    Returns:
        配置字典，文件不存在时返回空字典。
    """
    config_path = Path(os.getenv("APP_CONFIG_PATH", "config.yaml"))
    if not config_path.exists():
        return {}
    with config_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


class AppConfig(BaseSettings):
    """应用全局配置。

    所有配置项均可通过环境变量覆盖，环境变量命名规则：
    嵌套字段用双下划线连接，如 llm.provider → APP_LLM__PROVIDER。
    """

    model_config = SettingsConfigDict(
        env_prefix="APP_",
        env_nested_delimiter="__",
        extra="ignore",
        case_sensitive=False,
        env_file=".env",
        env_file_encoding="utf-8",
    )

    # === 应用基本信息 ===
    name: str = "multi-agent-orchestration-eval-platform"
    version: str = "0.1.0"
    debug: bool = False
    api_key: str | None = None

    # === 数据库 ===
    database_url: str = "sqlite+aiosqlite:///data/db/app.db"
    checkpoint_url: str = "sqlite+aiosqlite:///data/checkpoints/checkpoints.db"
    db_echo: bool = False

    # === Redis ===
    redis_url: str = "redis://localhost:6379/0"
    redis_cache_ttl: int = 30

    # === Celery ===
    celery_enabled: bool = False  # 修复Bug-001 改动原因：默认应为False，开发环境走同步降级，与README和config.yaml声明一致
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"
    celery_worker_concurrency: int = 4

    # === LLM ===
    llm_provider: str = "mock"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_model: str = "deepseek-chat"
    llm_temperature: float = 0.0
    llm_max_tokens: int = 4096
    llm_timeout: int = 60
    # 重试策略（T2-4）
    llm_retry_max_attempts: int = 3
    llm_retry_initial_delay: float = 1.0
    llm_retry_backoff_factor: float = 2.0
    # 预算控制（P1-9）
    budget_daily_limit: float = 200.0
    budget_warning_threshold: float = 180.0

    # === MCP 工具 ===
    mcp_tool_timeout: int = 30
    mcp_code_exec_timeout: int = 10
    mcp_max_file_size: int = 1048576  # 1MB
    mcp_max_query_rows: int = 1000

    # === Agent ===
    agent_max_rounds: int = 5
    agent_max_tool_calls: int = 5
    agent_max_revise_count: int = 2
    # 修复Bug-004 改动原因：添加任务结果缓存开关字段，AppConfig是Pydantic模型非字典，需通过属性访问
    task_result_cache_enabled: bool = True

    # === 评测 ===
    eval_cases_dir: str = "eval_cases"
    eval_judge_rounds: int = 2
    eval_pass_threshold: float = 6.0

    # === 日志 ===
    log_to_file: bool = False
    log_dir: str = "data/logs"
    log_retention_days: int = 7
    log_level: str = "INFO"
    log_json_format: bool = True

    # === 路径 ===
    data_dir: str = "data"
    db_dir: str = "data/db"
    checkpoint_dir: str = "data/checkpoints"
    reports_dir: str = "data/reports"
    workspace_dir: str = "data/workspace"


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    """获取全局配置单例（线程安全，lru_cache 保证只初始化一次）。

    配置加载优先级：环境变量 > config.yaml > 默认值。

    Returns:
        AppConfig 全局配置实例。
    """
    yaml_config = _load_yaml_config()

    # 将 YAML 嵌套结构展平为环境变量风格的键值对，
    # 作为 BaseSettings 的初始值（环境变量会覆盖这些值）。
    flat: dict[str, Any] = {}

    def _flatten(prefix: str, value: Any) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                key = f"{prefix}_{k}" if prefix else k
                _flatten(key, v)
        else:
            flat[prefix] = value

    _flatten("", yaml_config)

    return AppConfig(**flat)


def get_config_paths() -> dict[str, Path]:
    """获取所有数据目录的 Path 对象，并确保目录存在。

    Returns:
        目录名到 Path 的映射字典。
    """
    config = get_config()
    paths = {
        "data": Path(config.data_dir),
        "db": Path(config.db_dir),
        "checkpoint": Path(config.checkpoint_dir),
        "reports": Path(config.reports_dir),
        "workspace": Path(config.workspace_dir),
        "logs": Path(config.log_dir),
    }
    for p in paths.values():
        p.mkdir(parents=True, exist_ok=True)
    return paths
