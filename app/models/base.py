"""异步数据库引擎（T1-1）。

基于 SQLAlchemy 2.0 异步引擎 + aiosqlite 驱动：
- SQLite WAL 模式（PRAGMA journal_mode=WAL），允许读写并发
- busy_timeout=5000ms，写冲突时等待而非报错
- 异步 Session 工厂，请求级 Session 管理
- 检查点库独立文件（data/checkpoints/checkpoints.db）

架构决策（R-08）：SQLite 并发写入瓶颈通过 WAL + busy_timeout +
Worker concurrency ≤4 + 批量插入 + 原子递增缓解。
"""

from __future__ import annotations

from typing import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.logging import get_logger

logger = get_logger(__name__)


class Base(DeclarativeBase):
    """SQLAlchemy 声明式基类。

    所有 ORM 模型继承此类，通过 metadata 统一管理表结构。
    """

    pass


# 存储已创建的引擎实例（避免重复创建）
_engines: dict[str, object] = {}
_session_factories: dict[str, async_sessionmaker[AsyncSession]] = {}


def _apply_sqlite_pragmas(dbapi_connection: object, connection_record: object) -> None:
    """SQLite 连接事件监听器：设置 WAL 模式和 busy_timeout。

    每个新连接创建时调用，确保所有连接都使用 WAL 模式。

    Args:
        dbapi_connection: DBAPI 连接对象
        connection_record: 连接记录对象
    """
    cursor = dbapi_connection.cursor()
    # WAL 模式：允许读写并发（R-08）
    cursor.execute("PRAGMA journal_mode=WAL")
    # 写冲突时等待 5 秒而非立即报错
    cursor.execute("PRAGMA busy_timeout=5000")
    # 外键约束启用
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def get_engine(database_url: str, echo: bool = False) -> object:
    """获取或创建异步数据库引擎。

    同一 database_url 只创建一个引擎实例（单例模式）。

    Args:
        database_url: 数据库连接 URL（如 sqlite+aiosqlite:///data/db/app.db）
        echo: 是否输出 SQL 语句（调试用）

    Returns:
        SQLAlchemy 异步引擎实例
    """
    if database_url not in _engines:
        is_sqlite = "sqlite" in database_url
        # SQLite 使用 NullPool，不支持 pool_size/max_overflow；其他数据库才传连接池参数
        engine_kwargs = dict(
            echo=echo,
            pool_pre_ping=True,
        )
        if is_sqlite:
            engine_kwargs["connect_args"] = {"timeout": 5.0}
        else:
            engine_kwargs.update(
                pool_size=10,
                max_overflow=20,
            )
        engine = create_async_engine(database_url, **engine_kwargs)

        # SQLite 连接时设置 PRAGMA
        if "sqlite" in database_url:
            @event.listens_for(engine.sync_engine, "connect")
            def _on_connect(dbapi_connection: object, connection_record: object) -> None:
                _apply_sqlite_pragmas(dbapi_connection, connection_record)

        _engines[database_url] = engine
        logger.info("database_engine_created", url=database_url)

    return _engines[database_url]


def get_session_factory(database_url: str, echo: bool = False) -> async_sessionmaker[AsyncSession]:
    """获取或创建异步 Session 工厂。

    Args:
        database_url: 数据库连接 URL
        echo: 是否输出 SQL 语句

    Returns:
        异步 Session 工厂
    """
    if database_url not in _session_factories:
        engine = get_engine(database_url, echo)
        _session_factories[database_url] = async_sessionmaker(
            bind=engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factories[database_url]


async def init_database(database_url: str, echo: bool = False) -> None:
    """初始化数据库：创建全部表（T1-6）。

    开发期使用 create_all() 自动建表（P2-6 修正：第4周后引入 Alembic）。
    重复启动不报错（已存在的表跳过）。

    修复Bug-002 改动原因：create_all 使用 CREATE TABLE IF NOT EXISTS，不会修改已存在的表结构。
    模型新增 failed_cases 字段后，已有数据库表缺少该列导致所有评测接口500。
    在 create_all 后补充 SQLite 列迁移检查，缺失则 ALTER TABLE ADD COLUMN。

    Args:
        database_url: 数据库连接 URL
        echo: 是否输出 SQL 语句
    """
    # 导入所有模型以注册到 Base.metadata
    from app.models.eval import EvalCase, EvalReport, EvalRun, EvalScore
    from app.models.task import AgentMessage, Task, TaskExecution, ToolCall
    from app.models.usage import LLMUsage

    engine = get_engine(database_url, echo)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # 修复Bug-002 改动原因：SQLite 已有表结构不会被 create_all 更新，需手动补列
        if "sqlite" in database_url:
            from sqlalchemy import text

            # 检查 eval_runs 表是否存在 failed_cases 列
            result = await conn.execute(text("PRAGMA table_info(eval_runs)"))
            columns = {row[1] for row in result.fetchall()}
            if "failed_cases" not in columns:
                await conn.execute(
                    text("ALTER TABLE eval_runs ADD COLUMN failed_cases INTEGER NOT NULL DEFAULT 0")
                )
                logger.info("database_migration_added_column", table="eval_runs", column="failed_cases")

    logger.info("database_initialized", url=database_url, tables=len(Base.metadata.tables))


async def close_database(database_url: str) -> None:
    """关闭数据库引擎和连接池。

    应用关闭时调用，释放资源。

    Args:
        database_url: 数据库连接 URL
    """
    if database_url in _session_factories:
        del _session_factories[database_url]
    if database_url in _engines:
        engine = _engines.pop(database_url)
        await engine.dispose()
        logger.info("database_closed", url=database_url)


def async_session_factory() -> AsyncSession:
    """便捷函数：创建默认配置的异步 Session 实例。

    使用 config.database_url 创建 Session，可直接用于 async with：
        async with async_session_factory() as db:
            ...

    注意：每次调用创建新的 Session，调用方负责关闭（async with 自动关闭）。

    Returns:
        AsyncSession 实例（未绑定事务）
    """
    from app.config import get_config

    config = get_config()
    factory = get_session_factory(config.database_url, echo=config.db_echo)
    return factory()
