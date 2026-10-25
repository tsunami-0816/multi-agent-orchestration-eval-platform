"""任务数据访问层（T1-5）。

封装 tasks / task_executions / agent_messages / tool_calls 四张表的 CRUD 操作。
约束（T1-5）：Service 层禁止直接写 SQL，所有 DB 操作通过 Repository 完成。

核心操作：
- 任务创建/查询/状态更新/分页列表
- 执行记录批量插入
- Agent 消息批量插入
- 工具调用记录插入
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.task import AgentMessage, Task, TaskExecution, ToolCall

logger = get_logger(__name__)


class TaskRepository:
    """任务数据访问对象。

    封装任务相关的全部数据库操作，确保 Service 层不直接操作 SQLAlchemy。
    """

    def __init__(self, db: AsyncSession) -> None:
        """初始化 Repository。

        Args:
            db: 异步数据库 Session
        """
        self.db = db

    async def create(self, task: Task) -> Task:
        """创建新任务。

        Args:
            task: 任务 ORM 对象（id 应为 UUID v4）

        Returns:
            创建后的任务对象（已刷新）
        """
        self.db.add(task)
        await self.db.flush()
        await self.db.refresh(task)
        logger.info("task_created", task_id=task.id, scenario=task.scenario)
        return task

    async def get_by_id(self, task_id: str) -> Optional[Task]:
        """根据 ID 查询任务。

        Args:
            task_id: 任务 ID

        Returns:
            任务对象，不存在时返回 None
        """
        result = await self.db.execute(select(Task).where(Task.id == task_id))
        return result.scalar_one_or_none()

    async def update_status(
        self,
        task_id: str,
        status: str,
        error_message: Optional[str] = None,
        result: Optional[str] = None,
    ) -> Optional[Task]:
        """更新任务状态。

        Args:
            task_id: 任务 ID
            status: 新状态（PENDING/RUNNING/COMPLETED/FAILED/CANCELLED）
            error_message: 失败时的错误信息（可选）
            result: 完成时的结果内容（可选）

        Returns:
            更新后的任务对象，不存在时返回 None
        """
        task = await self.get_by_id(task_id)
        if not task:
            return None

        task.status = status
        if error_message is not None:
            task.error_message = error_message
        if result is not None:
            task.result = result

        # 状态流转时间戳
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if status == "running" and task.started_at is None:  # 修复-NEW-003 统一小写状态判断
            task.started_at = now
        if status in ("completed", "failed", "cancelled"):  # 修复-NEW-003 统一小写状态判断
            task.completed_at = now
            if task.started_at:
                task.duration_seconds = (now - task.started_at).total_seconds()

        await self.db.flush()
        logger.info("task_status_updated", task_id=task_id, status=status)
        return task

    async def set_cancellation_flag(self, task_id: str) -> bool:
        """设置取消请求标志（P1-4）。

        Worker 节点入口检查此标志，为 True 时优雅退出。

        Args:
            task_id: 任务 ID

        Returns:
            是否设置成功（任务不存在时返回 False）
        """
        task = await self.get_by_id(task_id)
        if not task:
            return False
        task.cancellation_requested = True
        await self.db.flush()
        logger.info("task_cancellation_flag_set", task_id=task_id)
        return True

    async def list_paginated(
        self,
        page: int = 1,
        page_size: int = 20,
        status: Optional[str] = None,
        scenario: Optional[str] = None,
    ) -> tuple[list[Task], int]:
        """分页查询任务列表。

        Args:
            page: 页码（从 1 开始）
            page_size: 每页数量（最大 100）
            status: 按状态筛选（可选）
            scenario: 按场景筛选（可选）

        Returns:
            (任务列表, 总数) 元组
        """
        page_size = min(page_size, 100)
        offset = (page - 1) * page_size

        query = select(Task)
        if status:
            query = query.where(Task.status == status)
        if scenario:
            query = query.where(Task.scenario == scenario)

        # 总数查询
        count_query = select(func.count()).select_from(query.subquery())
        total = (await self.db.execute(count_query)).scalar_one()

        # 分页查询
        query = query.order_by(Task.created_at.desc()).offset(offset).limit(page_size)
        result = await self.db.execute(query)
        tasks = list(result.scalars().all())

        return tasks, total

    async def bulk_insert_executions(self, executions: list[TaskExecution]) -> None:
        """批量插入任务执行记录。

        高频写入场景使用批量插入提升性能（R-08）。

        Args:
            executions: 执行记录对象列表
        """
        if not executions:
            return
        self.db.add_all(executions)
        await self.db.flush()
        logger.debug("task_executions_bulk_inserted", count=len(executions))

    async def bulk_insert_messages(self, messages: list[AgentMessage]) -> None:
        """批量插入 Agent 消息。

        Args:
            messages: Agent 消息对象列表
        """
        if not messages:
            return
        self.db.add_all(messages)
        await self.db.flush()
        logger.debug("agent_messages_bulk_inserted", count=len(messages))

    async def insert_tool_call(self, tool_call: ToolCall) -> ToolCall:
        """插入单条工具调用记录。

        Args:
            tool_call: 工具调用对象

        Returns:
            插入后的对象（已刷新）
        """
        self.db.add(tool_call)
        await self.db.flush()
        await self.db.refresh(tool_call)
        return tool_call

    async def get_executions_by_task(self, task_id: str) -> list[TaskExecution]:
        """查询任务的全部执行记录。

        Args:
            task_id: 任务 ID

        Returns:
            执行记录列表（按开始时间排序）
        """
        stmt = select(TaskExecution).where(TaskExecution.task_id == task_id).order_by(TaskExecution.created_at)  # 修复-NEW-001 TaskExecution无started_at字段，改用created_at
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_trace_messages(
        self,
        task_id: str,
        page: int = 1,
        page_size: int = 20,
        round_number: Optional[int] = None,
    ) -> tuple[list[AgentMessage], int]:
        """分页查询任务的 Agent 消息（trace 接口用，P2-3）。

        Args:
            task_id: 任务 ID
            page: 页码
            page_size: 每页数量
            round_number: 按轮次筛选（可选）

        Returns:
            (消息列表, 总数) 元组
        """
        page_size = min(page_size, 100)
        offset = (page - 1) * page_size

        query = select(AgentMessage).where(AgentMessage.task_id == task_id)
        if round_number is not None:
            query = query.where(AgentMessage.round_number == round_number)

        count_query = select(func.count()).select_from(query.subquery())
        total = (await self.db.execute(count_query)).scalar_one()

        query = query.order_by(AgentMessage.round_number, AgentMessage.sequence).offset(offset).limit(page_size)
        result = await self.db.execute(query)
        messages = list(result.scalars().all())

        return messages, total

    async def get_tool_calls(self, task_id: str) -> list[ToolCall]:
        """查询任务的全部工具调用记录。

        Args:
            task_id: 任务 ID

        Returns:
            工具调用记录列表（按时间升序）
        """
        query = select(ToolCall).where(ToolCall.task_id == task_id).order_by(ToolCall.created_at)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def commit(self) -> None:
        """提交事务。"""
        await self.db.commit()

    async def rollback(self) -> None:
        """回滚事务。"""
        await self.db.rollback()
