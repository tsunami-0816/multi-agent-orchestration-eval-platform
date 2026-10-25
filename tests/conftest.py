"""pytest 测试夹具（T12-1）。

提供测试环境的通用夹具：
- 临时 SQLite 数据库（隔离测试数据）
- Mock LLM Provider 夹具
- 异步数据库 Session 夹具
- FastAPI 测试客户端夹具
- Repository 夹具

所有测试使用临时数据库，不依赖真实 API 和外部服务。
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# 测试环境变量设置（必须在导入 app 模块之前）
os.environ["APP_LLM_PROVIDER"] = "mock"
os.environ["APP_DEBUG"] = "true"
os.environ["APP_LOG_LEVEL"] = "WARNING"


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """创建会话级事件循环。"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="function")
def temp_db_path() -> Generator[str, None, None]:
    """创建临时 SQLite 数据库文件路径。

    每个测试函数使用独立的临时文件，测试结束后自动删除。
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    yield f"sqlite+aiosqlite:///{db_path}"
    # 清理临时文件
    try:
        os.unlink(db_path)
        # WAL 模式可能产生 -wal 和 -shm 文件
        for suffix in ("-wal", "-shm"):
            extra = db_path + suffix
            if os.path.exists(extra):
                os.unlink(extra)
    except OSError:
        pass


@pytest_asyncio.fixture(scope="function")
async def db_engine(temp_db_path: str):
    """创建临时数据库引擎并初始化表结构。"""
    engine = create_async_engine(
        temp_db_path,
        echo=False,
        connect_args={"timeout": 5.0},
    )

    # 导入所有模型以注册到 metadata
    from app.models.base import Base
    from app.models.eval import EvalCase, EvalReport, EvalRun, EvalScore
    from app.models.task import AgentMessage, Task, TaskExecution, ToolCall
    from app.models.usage import LLMUsage

    # 创建全部表
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(db_engine) -> AsyncGenerator[AsyncSession, None]:
    """获取异步数据库 Session。"""
    factory = async_sessionmaker(
        bind=db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    async with factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture(scope="function")
async def task_repo(db_session: AsyncSession):
    """获取 TaskRepository 实例。"""
    from app.repositories.task_repo import TaskRepository

    return TaskRepository(db_session)


@pytest_asyncio.fixture(scope="function")
async def eval_repo(db_session: AsyncSession):
    """获取 EvalRepository 实例。"""
    from app.repositories.eval_repo import EvalRepository

    return EvalRepository(db_session)


@pytest_asyncio.fixture(scope="function")
async def usage_repo(db_session: AsyncSession):
    """获取 UsageRepository 实例。"""
    from app.repositories.usage_repo import UsageRepository

    return UsageRepository(db_session)


@pytest.fixture(scope="function")
def mock_llm_provider():
    """获取 Mock LLM Provider 实例。"""
    from app.llm.mock import MockProvider

    return MockProvider(model="test-mock")


@pytest.fixture(scope="function")
def app_config():
    """获取测试用配置（Mock 模式）。"""
    from app.config import AppConfig

    return AppConfig(
        llm_provider="mock",
        database_url="sqlite+aiosqlite:///:memory:",
        debug=True,
        log_level="WARNING",
    )


@pytest_asyncio.fixture(scope="function")
async def client() -> AsyncGenerator[AsyncClient, None]:
    """获取 FastAPI 异步测试客户端。

    使用 httpx.AsyncClient + ASGITransport，无需启动真实服务器。
    """
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
