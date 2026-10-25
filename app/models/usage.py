"""LLM 用量表 ORM 模型（T1-4，P1-9 新增）。

定义 llm_usage 表，记录每次 LLM 调用的 Token 消耗和费用：
- 用于累计费用查询和预算控制（P1-9）
- 超 ¥180 告警 + 新任务强制 Mock
- 超 ¥200 拒绝新任务返回 50003

字段与架构 §4.2.9 完全一致。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class LLMUsage(Base):
    """LLM 用量记录表（架构 §4.2.9，P1-9 新增）。

    每次 LLM API 调用写入一条记录，用于：
    - 累计费用查询：SELECT SUM(cost_yuan) WHERE date(created_at) = date('now')
    - 预算控制：超过 ¥180 告警 + 强制 Mock，超过 ¥200 拒绝
    - 成本分析：按模型/按任务统计 Token 消耗
    """

    __tablename__ = "llm_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
        comment="关联任务（评测 Judge 调用可能无 task_id）",
    )
    model: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="模型标识（deepseek-chat/qwen-turbo/ollama/mock）"
    )
    input_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="输入 Token 数"
    )
    output_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="输出 Token 数"
    )
    cost_yuan: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, comment="本次调用费用（元）"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), comment="调用时间"
    )

    __table_args__ = (
        Index("idx_llm_usage_date", "created_at"),
        Index("idx_llm_usage_model", "model"),
    )
