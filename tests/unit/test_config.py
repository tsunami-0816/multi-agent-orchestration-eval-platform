"""配置模块单元测试（T0-2）。

测试 Pydantic Settings 配置加载、环境变量覆盖、默认值。
"""

from __future__ import annotations

import os

import pytest


class TestConfig:
    """配置模块测试。"""

    def test_default_config_values(self) -> None:
        """测试默认配置值（不读 yaml/.env，只验证代码默认值）。"""
        from app.config import AppConfig

        # _env_file=None 跳过 .env；yaml 由 get_config() 手动加载，AppConfig() 直接实例化不读 yaml
        config = AppConfig(_env_file=None)
        assert config.name == "multi-agent-orchestration-eval-platform"
        assert config.agent_max_rounds == 5
        assert config.agent_max_tool_calls == 5
        assert config.celery_worker_concurrency == 4

    def test_env_variable_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """测试环境变量覆盖配置。"""
        monkeypatch.setenv("APP_LLM_PROVIDER", "deepseek")
        monkeypatch.setenv("APP_AGENT_MAX_ROUNDS", "3")
        monkeypatch.setenv("APP_BUDGET_DAILY_LIMIT", "100.5")

        from app.config import AppConfig

        config = AppConfig()
        assert config.llm_provider == "deepseek"
        assert config.agent_max_rounds == 3
        assert config.budget_daily_limit == 100.5

    def test_get_config_singleton(self) -> None:
        """测试 get_config 返回单例。"""
        from app.config import get_config

        config1 = get_config()
        config2 = get_config()
        assert config1 is config2

    def test_get_config_paths_creates_dirs(self, tmp_path: object) -> None:
        """测试 get_config_paths 创建数据目录。"""
        from app.config import AppConfig, get_config_paths

        # 使用临时目录
        config = AppConfig(
            data_dir=str(tmp_path / "data"),
            db_dir=str(tmp_path / "data" / "db"),
            checkpoint_dir=str(tmp_path / "data" / "checkpoints"),
            reports_dir=str(tmp_path / "data" / "reports"),
            workspace_dir=str(tmp_path / "data" / "workspace"),
            logs_dir=str(tmp_path / "data" / "logs"),
        )
        # 临时替换全局配置
        import app.config as config_module

        original = config_module.get_config
        config_module.get_config = lambda: config  # type: ignore

        try:
            paths = get_config_paths()
            assert "data" in paths
            assert "db" in paths
            assert paths["db"].exists()
            assert paths["checkpoint"].exists()
        finally:
            config_module.get_config = original  # type: ignore
