"""编排任务（T5-2）。

Celery 任务：异步执行 Agent 编排流程。
- 支持取消（T5-4）：通过数据库取消标志
- 支持回调（T5-6）：任务完成后调用 webhook
- 支持结果缓存（T5-7）：相同输入缓存结果

架构决策（T-01）：长耗时编排任务异步执行，
API 立即返回 task_id，客户端轮询或等待回调。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from app.agent.orchestrator import AgentOrchestrator
from app.core.logging import get_logger
from app.models.base import async_session_factory
from app.models.task import Task, TaskExecution
from app.repositories.task_repo import TaskRepository
from app.workers.celery_app import celery_app, is_celery_enabled

logger = get_logger(__name__)


def _run_orchestration_sync(
    task_id: str,
    user_input: str,
    trace_id: str = "",
    max_rounds: int = 5,
    callback_url: Optional[str] = None,
) -> dict[str, Any]:
    """同步执行编排流程（Celery 任务的核心逻辑）。

    封装为同步函数，便于 Celery（同步）和直接调用。

    Args:
        task_id: 任务 ID
        user_input: 用户输入
        trace_id: 链路追踪 ID
        max_rounds: 最大轮次
        callback_url: 回调 URL（T5-6）

    Returns:
        执行结果字典
    """
    logger.info("orchestration_task_start", task_id=task_id)

    # 更新任务状态为运行中
    asyncio.run(_update_task_status(task_id, "running"))

    try:
        # 修复-BUG-001/005 开启MCP工具调用，Executor节点可使用web_search/code_exec/file_rw/db_query
        orchestrator = AgentOrchestrator(use_mcp=True, checkpoint_enabled=True)

        # 执行编排（在同步函数中运行异步）
        final_state = asyncio.run(
            orchestrator.run(
                user_input=user_input,
                task_id=task_id,
                trace_id=trace_id,
                max_rounds=max_rounds,
            )
        )

        result = {
            "task_id": task_id,
            "status": final_state.get("status", "completed"),
            "final_output": final_state.get("final_output", ""),
            "subtask_count": len(final_state.get("subtasks", [])),
            "round_count": final_state.get("round_count", 0),
            "error": final_state.get("error"),
        }

        # 更新任务状态（修复-NEW-002 设置started_at/duration_seconds）
        asyncio.run(_update_task_status(task_id, result["status"], result.get("final_output", "")))

        # 修复-BUG-017 从DB读取最新task，补充completed_at/duration_seconds到回调载荷
        try:
            async def _load_task_for_callback():
                async with async_session_factory() as db:
                    repo = TaskRepository(db)
                    # 修复Bug-REG2-001 改动原因：TaskRepository实际方法名为get_by_id，原get_task_by_id不存在导致AttributeError
                    return await repo.get_by_id(task_id)
            task = asyncio.run(_load_task_for_callback())
            if task:
                if task.completed_at:
                    result["completed_at"] = task.completed_at.isoformat()
                if task.duration_seconds is not None:
                    result["duration_seconds"] = task.duration_seconds
        except Exception as e:
            logger.warning("load_task_for_callback_failed", task_id=task_id, error=str(e))

        # 回调通知（T5-6）
        if callback_url:
            asyncio.run(_send_callback(callback_url, result))

        logger.info("orchestration_task_complete", task_id=task_id, status=result["status"])
        return result

    except Exception as e:
        logger.error("orchestration_task_failed", task_id=task_id, error=str(e))
        asyncio.run(_update_task_status(task_id, "failed", error=str(e)))
        return {
            "task_id": task_id,
            "status": "failed",
            "error": str(e),
            "final_output": "",
        }


async def _update_task_status(
    task_id: str, status: str, result: str = "", error: Optional[str] = None
) -> None:
    """更新任务状态到数据库。

    修复-NEW-002: running状态设置started_at，终态计算duration_seconds
    修复-BUG-020: 状态更新后主动失效Redis缓存

    Args:
        task_id: 任务 ID
        status: 新状态
        result: 结果文本
        error: 错误信息
    """
    try:
        async with async_session_factory() as db:
            repo = TaskRepository(db)
            # 修复Bug-REG2-001 改动原因：TaskRepository实际方法名为get_by_id，原get_task_by_id不存在导致AttributeError，状态更新静默失败
            task = await repo.get_by_id(task_id)
            if task:
                task.status = status
                if result:
                    task.result = result[:5000]
                if error:
                    task.error_message = error[:1000]

                # 修复-NEW-002 设置开始时间
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                if status == "running" and task.started_at is None:
                    task.started_at = now

                # 修复-NEW-002 终态设置完成时间和耗时
                if status in ("completed", "failed", "cancelled"):
                    task.completed_at = now
                    if task.started_at:
                        task.duration_seconds = (now - task.started_at).total_seconds()

                await repo.commit()

                # 修复-BUG-020 状态更新后主动失效Redis缓存
                _invalidate_task_cache(task_id)
    except Exception as e:
        logger.error("update_task_status_failed", task_id=task_id, error=str(e))


def _invalidate_task_cache(task_id: str) -> None:
    """修复-BUG-020 主动失效任务状态的Redis缓存。

    Args:
        task_id: 任务 ID
    """
    try:
        import redis
        from app.config import get_config
        config = get_config()
        r = redis.from_url(config.redis_url)
        r.delete(f"task:{task_id}")
        r.close()
        logger.debug("task_cache_invalidated", task_id=task_id)
    except Exception as e:
        logger.debug("task_cache_invalidate_skipped", task_id=task_id, error=str(e))


async def _send_callback(callback_url: str, result: dict[str, Any]) -> None:
    """发送任务完成回调（T5-6）。

    修复-BUG-016: 改用httpx异步客户端，添加指数退避重试（1s→3s→9s，最多3次）
    修复-BUG-018: 请求头添加X-Task-Id

    Args:
        callback_url: 回调 URL
        result: 任务结果
    """
    import httpx

    task_id = result.get("task_id", "")
    # 修复-BUG-018 添加X-Task-Id请求头
    headers = {
        "Content-Type": "application/json",
        "X-Task-Id": task_id,
    }
    data = json.dumps(result, ensure_ascii=False).encode("utf-8")

    # 修复-BUG-016 指数退避重试：1s → 3s → 9s，最多3次（含首次）
    retry_delays = [1, 3, 9]
    max_attempts = len(retry_delays) + 1

    async with httpx.AsyncClient(timeout=10.0) as client:
        for attempt in range(max_attempts):
            try:
                resp = await client.post(callback_url, content=data, headers=headers)
                if resp.status_code < 400:
                    logger.info("callback_sent", url=callback_url, status=resp.status_code, attempt=attempt + 1)
                    return
                logger.warning("callback_non_2xx", url=callback_url, status=resp.status_code, attempt=attempt + 1)
            except Exception as e:
                logger.warning("callback_failed", url=callback_url, error=str(e), attempt=attempt + 1)

            # 不是最后一次尝试则等待后退避
            if attempt < len(retry_delays):
                delay = retry_delays[attempt]
                logger.info("callback_retry_wait", url=callback_url, delay=delay, attempt=attempt + 1)
                await asyncio.sleep(delay)

    logger.error("callback_exhausted", url=callback_url, task_id=task_id)


# Celery 任务定义（仅在 Celery 可用时注册）
if is_celery_enabled():

    @celery_app.task(bind=True, name="orchestration.run", max_retries=2)
    def run_orchestration_task(
        self,
        task_id: str,
        user_input: str,
        trace_id: str = "",
        max_rounds: int = 5,
        callback_url: Optional[str] = None,
    ) -> dict[str, Any]:
        """Celery 任务：执行 Agent 编排。

        Args:
            task_id: 任务 ID
            user_input: 用户输入
            trace_id: 链路追踪 ID
            max_rounds: 最大轮次
            callback_url: 回调 URL

        Returns:
            执行结果字典
        """
        return _run_orchestration_sync(
            task_id=task_id,
            user_input=user_input,
            trace_id=trace_id,
            max_rounds=max_rounds,
            callback_url=callback_url,
        )

else:
    # 同步降级模式：提供同名函数
    def run_orchestration_task(
        task_id: str,
        user_input: str,
        trace_id: str = "",
        max_rounds: int = 5,
        callback_url: Optional[str] = None,
    ) -> dict[str, Any]:
        """同步执行编排任务（Celery 不可用时的降级）。"""
        return _run_orchestration_sync(
            task_id=task_id,
            user_input=user_input,
            trace_id=trace_id,
            max_rounds=max_rounds,
            callback_url=callback_url,
        )
