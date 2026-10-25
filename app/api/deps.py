"""FastAPI 依赖注入框架（T0-6）。

提供请求级数据库 Session 管理和 Service 实例获取：
- get_db_session：请求级异步 Session，请求结束后自动关闭
- get_task_service / get_eval_service 等：通过 Session 初始化 Service

架构决策：所有 DB 操作通过 Repository 层完成，
Service 层不直接写 SQL（T1-5 约束）。
"""

from __future__ import annotations

from typing import AsyncGenerator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_config
from app.core.logging import get_logger
from app.models.base import get_session_factory

logger = get_logger(__name__)

# 全局 Session 工厂（懒加载，首次调用时初始化）
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _get_session_factory() -> async_sessionmaker[AsyncSession]:
    """获取或初始化全局 Session 工厂。

    Returns:
        SQLAlchemy 异步 Session 工厂
    """
    global _session_factory
    if _session_factory is None:
        config = get_config()
        _session_factory = get_session_factory(config.database_url)
    return _session_factory


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """获取请求级数据库 Session（FastAPI 依赖）。

    请求结束后自动关闭 Session，确保连接释放。

    Yields:
        AsyncSession: 异步数据库会话
    """
    factory = _get_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# === Service 依赖（后续模块实现后补充）===

# 注意：TaskService / EvalService 在 M5/M6 模块中实现，
# 此处预留依赖注入接口，避免循环导入。
# 实际实现时取消注释并导入对应 Service。

# async def get_task_service(
#     db: AsyncSession = Depends(get_db_session),
# ) -> TaskService:
#     """获取 TaskService 实例。"""
#     return TaskService(db)
#
#
# async def get_eval_service(
#     db: AsyncSession = Depends(get_db_session),
# ) -> EvalService:
#     """获取 EvalService 实例。"""
#     return EvalService(db)
#
#
# async def get_llm_gateway() -> LLMGateway:
#     """获取 LLMGateway 单例。"""
#     from app.llm.gateway import get_gateway
#     return get_gateway()


def get_request_id(request: object) -> str:
    """从请求对象获取 trace_id 作为 request_id。

    Args:
        request: FastAPI Request 对象

    Returns:
        trace_id 字符串
    """
    return getattr(request.state, "trace_id", "unknown")
