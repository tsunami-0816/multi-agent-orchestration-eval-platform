"""任务服务（T5-3）。

封装任务的完整生命周期管理：
- 创建任务（T5-3）：写入数据库 + 提交 Celery 任务
- 查询任务（T5-3）：状态/结果/消息
- 取消任务（T5-4）：设置取消标志，Agent 节点检查后停止
- 恢复任务（T5-5）：从检查点恢复执行
- 任务列表（分页）
- 结果缓存（T5-7）：相同输入的任务缓存结果

架构决策（T-01）：TaskService 为无状态服务，
通过数据库和 Celery 管理任务状态，支持横向扩展。
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from app.config import get_config
from app.core.exceptions import AppException, ErrorCode
from app.core.logging import get_logger
from app.models.base import async_session_factory
from app.models.task import Task, TaskExecution
from app.repositories.task_repo import TaskRepository
from app.workers.celery_app import celery_app, is_celery_enabled
from app.workers.tasks.orchestration import run_orchestration_task

logger = get_logger(__name__)

# 内存结果缓存（T5-7）：input_hash -> result
_result_cache: dict[str, dict[str, Any]] = {}
_CACHE_MAX_SIZE = 1000


class TaskService:
    """任务服务（T5-3）。

    管理任务的创建、查询、取消、恢复等操作。
    """

    def __init__(self) -> None:
        """初始化任务服务。"""
        self.config = get_config()

    async def create_task(
        self,
        user_input: str,
        task_type: str = "orchestration",
        callback_url: Optional[str] = None,
        max_rounds: int = 5,
        trace_id: str = "",
    ) -> Task:
        """创建并提交任务（T5-3）。

        Args:
            user_input: 用户输入
            task_type: 任务类型（orchestration/eval）
            callback_url: 完成回调 URL（T5-6）
            max_rounds: 最大轮次
            trace_id: 链路追踪 ID

        Returns:
            创建的 Task 对象

        Raises:
            AppException(VALIDATION_ERROR): 输入为空时抛出
        """
        if not user_input or not user_input.strip():
            raise AppException(ErrorCode.VALIDATION_ERROR, "任务输入不能为空")

        task_id = str(uuid.uuid4())

        # 检查结果缓存（T5-7）
        input_hash = self._hash_input(user_input)
        cached = _result_cache.get(input_hash)
        # 修复Bug-004 改动原因：self.config是AppConfig(Pydantic BaseSettings)实例，不是字典，没有.get()方法，改用getattr
        if cached and getattr(self.config, "task_result_cache_enabled", True):
            logger.info("task_cache_hit", input_hash=input_hash)
            # 缓存命中：直接创建已完成任务
            task = Task(
                id=task_id,
                scenario=task_type,
                input=user_input,
                status="completed",
                result=cached.get("final_output", ""),
                callback_url=callback_url or "",
                created_at=datetime.now(timezone.utc).replace(tzinfo=None),
                completed_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
            async with async_session_factory() as db:
                repo = TaskRepository(db)
                await repo.create(task)
                await repo.commit()
            return task

        # 创建任务记录
        task = Task(
            id=task_id,
            scenario=task_type,
            input=user_input,
            status="pending",
            callback_url=callback_url or "",
            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )

        async with async_session_factory() as db:
            repo = TaskRepository(db)
            await repo.create(task)
            await repo.commit()

        # 提交异步任务
        if is_celery_enabled():
            # Celery 异步执行
            run_orchestration_task.delay(
                task_id=task_id,
                user_input=user_input,
                trace_id=trace_id,
                max_rounds=max_rounds,
                callback_url=callback_url,
            )
            logger.info("task_submitted_celery", task_id=task_id)
        else:
            # 同步降级执行（开发环境）
            logger.info("task_submitted_sync", task_id=task_id)
            import asyncio
            asyncio.get_event_loop().run_in_executor(
                None,
                lambda: run_orchestration_task(
                    task_id=task_id,
                    user_input=user_input,
                    trace_id=trace_id,
                    max_rounds=max_rounds,
                    callback_url=callback_url,
                ),
            )

        return task

    async def get_task(self, task_id: str) -> Task:
        """获取任务详情。

        修复-BUG-019: 添加Redis缓存，稳定状态（completed/failed/cancelled）缓存30秒，
        RUNNING/PENDING状态不缓存（需要实时状态）

        Args:
            task_id: 任务 ID

        Returns:
            Task 对象

        Raises:
            AppException(TASK_NOT_FOUND): 任务不存在时抛出
        """
        # 修复-BUG-019 尝试从Redis缓存读取
        cached_task = self._get_task_from_cache(task_id)
        if cached_task is not None:
            logger.debug("task_cache_hit", task_id=task_id)
            return cached_task

        async with async_session_factory() as db:
            repo = TaskRepository(db)
            task = await repo.get_by_id(task_id)
            if not task:
                raise AppException(ErrorCode.TASK_NOT_FOUND, f"任务 {task_id} 不存在")

            # 修复-BUG-019 稳定状态写入缓存，RUNNING/PENDING不缓存
            if task.status in ("completed", "failed", "cancelled"):
                self._set_task_to_cache(task_id, task)

            return task

    def _get_task_from_cache(self, task_id: str) -> Optional[Task]:
        """修复-BUG-019 从Redis缓存读取任务。

        Args:
            task_id: 任务 ID

        Returns:
            Task对象，缓存未命中或Redis不可用时返回None
        """
        try:
            import redis
            import pickle
            config = get_config()
            r = redis.from_url(config.redis_url)
            data = r.get(f"task:{task_id}")
            r.close()
            if data:
                return pickle.loads(data)
        except Exception as e:
            logger.debug("task_cache_read_failed", task_id=task_id, error=str(e))
        return None

    def _set_task_to_cache(self, task_id: str, task: Task) -> None:
        """修复-BUG-019 将任务写入Redis缓存（TTL=30s）。

        Args:
            task_id: 任务 ID
            task: Task对象
        """
        try:
            import redis
            import pickle
            config = get_config()
            r = redis.from_url(config.redis_url)
            r.setex(f"task:{task_id}", config.redis_cache_ttl, pickle.dumps(task))
            r.close()
            logger.debug("task_cache_set", task_id=task_id, ttl=config.redis_cache_ttl)
        except Exception as e:
            logger.debug("task_cache_write_failed", task_id=task_id, error=str(e))

    async def list_tasks(
        self,
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Task], int]:
        """分页查询任务列表。

        Args:
            status: 按状态筛选（可选）
            page: 页码（从 1 开始）
            page_size: 每页数量

        Returns:
            (任务列表, 总数)
        """
        async with async_session_factory() as db:
            repo = TaskRepository(db)
            tasks, total = await repo.list_paginated(status=status, page=page, page_size=page_size)
            return tasks, total

    async def cancel_task(self, task_id: str) -> Task:
        """取消任务（T5-4）。

        修复-BUG-013: 区分PENDING和RUNNING状态
        - PENDING: 调用Celery revoke + 直接设cancelled
        - RUNNING: 设置cancellation_requested标志，Agent优雅退出
        修复-BUG-015: RUNNING状态取消后启动30秒超时强制终止

        Args:
            task_id: 任务 ID

        Returns:
            更新后的 Task 对象

        Raises:
            AppException(TASK_NOT_FOUND): 任务不存在时抛出
            AppException(TASK_STATUS_ERROR): 任务已结束无法取消时抛出
        """
        async with async_session_factory() as db:
            repo = TaskRepository(db)
            task = await repo.get_by_id(task_id)
            if not task:
                raise AppException(ErrorCode.TASK_NOT_FOUND, f"任务 {task_id} 不存在")

            if task.status in ("completed", "failed", "cancelled"):
                raise AppException(
                    ErrorCode.TASK_STATUS_ERROR,
                    f"任务已处于 {task.status} 状态，无法取消",
                )

            now = datetime.now(timezone.utc).replace(tzinfo=None)

            if task.status == "pending":
                # 修复-BUG-013 PENDING状态：从队列移除 + 直接标记取消
                if is_celery_enabled() and celery_app is not None:
                    try:
                        celery_app.control.revoke(task_id, terminate=False)
                        logger.info("task_revoked", task_id=task_id)
                    except Exception as e:
                        logger.warning("task_revoke_failed", task_id=task_id, error=str(e))

                task.status = "cancelled"
                task.cancellation_requested = True
                task.completed_at = now
                await repo.commit()
                logger.info("task_cancelled_pending", task_id=task_id)

            else:  # running
                # 修复-BUG-013 RUNNING状态：设置取消标志，等待优雅退出
                task.cancellation_requested = True
                await repo.commit()
                logger.info("task_cancellation_requested", task_id=task_id)

                # 修复-BUG-015 启动30秒超时强制终止监控
                self._schedule_force_terminate(task_id, timeout=30)

            return task

    def _schedule_force_terminate(self, task_id: str, timeout: int = 30) -> None:
        """修复-BUG-015 调度30秒后的强制终止检查。

        Args:
            task_id: 任务 ID
            timeout: 超时秒数
        """
        def _force_terminate_check():
            """超时后检查任务状态，若仍在running则强制终止。"""
            import asyncio
            try:
                async def _check_and_terminate():
                    async with async_session_factory() as db:
                        repo = TaskRepository(db)
                        task = await repo.get_by_id(task_id)
                        if task and task.status == "running":
                            # 强制终止Celery worker进程
                            if is_celery_enabled() and celery_app is not None:
                                try:
                                    celery_app.control.terminate(task_id, signal="SIGTERM")
                                    logger.info("task_force_terminated", task_id=task_id)
                                except Exception as e:
                                    logger.warning("task_terminate_failed", task_id=task_id, error=str(e))

                            # 更新任务状态为cancelled
                            task.status = "cancelled"
                            task.error_message = "取消超时，强制终止"
                            task.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                            if task.started_at:
                                task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                            await repo.commit()
                            logger.info("task_force_cancelled", task_id=task_id)

                asyncio.run(_check_and_terminate())
            except Exception as e:
                logger.error("force_terminate_check_failed", task_id=task_id, error=str(e))

        timer = threading.Timer(timeout, _force_terminate_check)
        timer.daemon = True
        timer.start()
        logger.info("force_terminate_scheduled", task_id=task_id, timeout=timeout)

    async def get_task_messages(self, task_id: str, limit: int = 100) -> list[Any]:
        """获取任务的 Agent 消息历史。

        Args:
            task_id: 任务 ID
            limit: 最大返回条数

        Returns:
            消息列表
        """
        async with async_session_factory() as db:
            repo = TaskRepository(db)
            messages, _ = await repo.get_trace_messages(task_id, page=1, page_size=limit)
            return messages

    async def get_task_executions(self, task_id: str) -> list[Any]:
        """获取任务的执行记录。

        Args:
            task_id: 任务 ID

        Returns:
            执行记录列表
        """
        async with async_session_factory() as db:
            repo = TaskRepository(db)
            return await repo.get_executions_by_task(task_id)

    async def resume_task(self, task_id: str) -> Task:
        """修复-BUG-022 恢复任务（从FAILED/CANCELLED状态重新执行）。

        验证任务状态为FAILED/CANCELLED，重置状态为pending，
        清除错误信息，提交Celery任务重新执行。

        Args:
            task_id: 任务 ID

        Returns:
            更新后的 Task 对象

        Raises:
            AppException(TASK_NOT_FOUND): 任务不存在时抛出
            AppException(TASK_STATUS_ERROR): 任务状态不允许恢复时抛出
        """
        async with async_session_factory() as db:
            repo = TaskRepository(db)
            task = await repo.get_by_id(task_id)
            if not task:
                raise AppException(ErrorCode.TASK_NOT_FOUND, f"任务 {task_id} 不存在")

            if task.status == "completed":
                raise AppException(
                    ErrorCode.TASK_STATUS_ERROR,
                    "已完成任务不可恢复",
                )

            if task.status not in ("failed", "cancelled"):
                raise AppException(
                    ErrorCode.TASK_STATUS_ERROR,
                    f"任务处于 {task.status} 状态，仅 failed/cancelled 状态可恢复",
                )

            # 重置任务状态
            task.status = "pending"
            task.error_message = None
            task.cancellation_requested = False
            task.started_at = None
            task.completed_at = None
            task.duration_seconds = None
            await repo.commit()

            logger.info("task_resumed", task_id=task_id, previous_status=task.status)

        # 提交异步任务重新执行
        if is_celery_enabled():
            run_orchestration_task.delay(
                task_id=task_id,
                user_input=task.input,
                max_rounds=task.max_rounds,
                callback_url=task.callback_url or None,
            )
            logger.info("task_resume_submitted_celery", task_id=task_id)
        else:
            import asyncio
            asyncio.get_event_loop().run_in_executor(
                None,
                lambda: run_orchestration_task(
                    task_id=task_id,
                    user_input=task.input,
                    max_rounds=task.max_rounds,
                    callback_url=task.callback_url or None,
                ),
            )
            logger.info("task_resume_submitted_sync", task_id=task_id)

        return task

    @staticmethod
    def _hash_input(user_input: str) -> str:
        """计算输入的哈希值（用于结果缓存 T5-7）。

        Args:
            user_input: 用户输入

        Returns:
            SHA256 哈希值
        """
        return hashlib.sha256(user_input.strip().encode("utf-8")).hexdigest()

    @staticmethod
    def cache_result(input_hash: str, result: dict[str, Any]) -> None:
        """缓存任务结果（T5-7）。

        Args:
            input_hash: 输入哈希
            result: 结果字典
        """
        if len(_result_cache) >= _CACHE_MAX_SIZE:
            # 简单淘汰：删除最早的一条
            oldest_key = next(iter(_result_cache))
            del _result_cache[oldest_key]
        _result_cache[input_hash] = result
