"""LLM 用量数据访问层（T1-5，P1-9）。

封装 llm_usage 表的操作，支持：
- 用量记录插入
- 当日累计费用查询（预算控制用）
- 按模型/日期统计
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.usage import LLMUsage

logger = get_logger(__name__)


class UsageRepository:
    """LLM 用量数据访问对象。"""

    def __init__(self, db: AsyncSession) -> None:
        """初始化 Repository。

        Args:
            db: 异步数据库 Session
        """
        self.db = db

    async def insert_usage(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_yuan: float,
        task_id: Optional[str] = None,
    ) -> LLMUsage:
        """插入一条 LLM 用量记录。

        Args:
            model: 模型标识
            input_tokens: 输入 Token 数
            output_tokens: 输出 Token 数
            cost_yuan: 本次调用费用（元）
            task_id: 关联任务 ID（可选）

        Returns:
            插入后的对象（已刷新）
        """
        usage = LLMUsage(
            task_id=task_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_yuan=cost_yuan,
        )
        self.db.add(usage)
        await self.db.flush()
        await self.db.refresh(usage)
        return usage

    async def get_daily_cost(self, target_date: Optional[date] = None) -> float:
        """查询指定日期的累计 LLM 费用（P1-9 预算控制用）。

        Args:
            target_date: 查询日期，默认为当天（UTC）

        Returns:
            累计费用（元）
        """
        if target_date is None:
            target_date = datetime.now(timezone.utc).date()

        query = select(func.coalesce(func.sum(LLMUsage.cost_yuan), 0.0)).where(
            func.date(LLMUsage.created_at) == target_date.isoformat()
        )
        result = await self.db.execute(query)
        total = float(result.scalar_one())
        return total

    async def get_daily_usage_by_model(self, target_date: Optional[date] = None) -> list[dict]:
        """按模型分组查询当日用量统计。

        Args:
            target_date: 查询日期，默认为当天

        Returns:
            按模型分组的统计列表，每项含 model/calls/input_tokens/output_tokens/cost_yuan
        """
        if target_date is None:
            target_date = datetime.now(timezone.utc).date()

        query = (
            select(
                LLMUsage.model,
                func.count(LLMUsage.id).label("calls"),
                func.coalesce(func.sum(LLMUsage.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(LLMUsage.output_tokens), 0).label("output_tokens"),
                func.coalesce(func.sum(LLMUsage.cost_yuan), 0.0).label("cost_yuan"),
            )
            .where(func.date(LLMUsage.created_at) == target_date.isoformat())
            .group_by(LLMUsage.model)
        )
        result = await self.db.execute(query)
        return [
            {
                "model": row.model,
                "calls": row.calls,
                "input_tokens": row.input_tokens,
                "output_tokens": row.output_tokens,
                "cost_yuan": float(row.cost_yuan),
            }
            for row in result.all()
        ]

    async def commit(self) -> None:
        """提交事务。"""
        await self.db.commit()

    async def rollback(self) -> None:
        """回滚事务。"""
        await self.db.rollback()
