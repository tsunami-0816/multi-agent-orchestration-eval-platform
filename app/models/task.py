"""任务相关表 ORM 模型（T1-2）。

定义四张表：
- tasks：任务主表（含 cancellation_requested/callback_url，P1-4/P1-5）
- task_executions：任务执行记录表（LangGraph 节点级）
- agent_messages：Agent 间消息表（trace 回放用）
- tool_calls：工具调用记录表

字段类型/约束/索引与架构 §4.2.1~4.2.4 完全一致。
外键级联策略：tasks 删除时级联删除关联记录（架构 §4.4）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Task(Base):
    """任务主表（架构 §4.2.1）。

    存储用户提交的编排任务，包含状态流转、回调配置和评测关联。
    """

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, comment="UUID v4")
    input: Mapped[str] = mapped_column(Text, nullable=False, comment="用户原始输入")
    scenario: Mapped[str] = mapped_column(
        String(64), nullable=False, default="default", comment="场景标识"
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", comment="pending/running/completed/failed/cancelled"  # 修复-BUG-002 统一小写状态值，与代码写入一致
    )
    result: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="最终输出内容")
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="失败时的错误信息")
    max_rounds: Mapped[int] = mapped_column(Integer, nullable=False, default=5, comment="最大协作轮次")
    current_round: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="当前执行轮次")
    # P1-4 新增：取消请求标志，Worker 节点入口检查
    cancellation_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="取消请求标志"
    )
    # P1-5 新增：任务完成回调 URL
    callback_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True, comment="回调 URL")
    eval_run_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("eval_runs.id", ondelete="SET NULL"), nullable=True, comment="关联评测运行"
    )
    eval_case_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("eval_cases.id", ondelete="SET NULL"), nullable=True, comment="关联评测用例"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), comment="创建时间（UTC）"
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, comment="开始执行时间")
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, comment="完成时间")
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="执行耗时")

    # 关联关系（级联删除）
    executions: Mapped[list["TaskExecution"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )
    messages: Mapped[list["AgentMessage"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )
    tool_calls: Mapped[list["ToolCall"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_tasks_status", "status"),
        Index("idx_tasks_created_at", "created_at"),
        Index("idx_tasks_scenario", "scenario"),
        Index("idx_tasks_eval_run", "eval_run_id"),
    )


class TaskExecution(Base):
    """任务执行记录表（架构 §4.2.2）。

    记录 LangGraph 每个节点的输入输出，用于调试和 trace。
    """

    __tablename__ = "task_executions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    agent_role: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="planner/executor/reviewer/aggregator"
    )
    node_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="LangGraph 节点名")
    input_data: Mapped[str] = mapped_column(Text, nullable=False, comment="节点输入（JSON）")
    output_data: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="节点输出（JSON）")
    round_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1, comment="第几轮协作")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), comment="执行时间"
    )

    task: Mapped["Task"] = relationship(back_populates="executions")

    __table_args__ = (
        Index("idx_exec_task_id", "task_id", "round_number"),
    )


class AgentMessage(Base):
    """Agent 间消息表（架构 §4.2.3）。

    记录 Agent 之间传递的所有消息，用于 trace 回放（M-04 100% 可追溯）。
    """

    __tablename__ = "agent_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="system/user/assistant/tool"
    )
    agent_role: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True, comment="发送方 Agent 角色"
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="消息内容")
    message_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="text", comment="text/tool_call/tool_result"
    )
    round_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1, comment="协作轮次")
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, comment="轮次内消息序号")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), comment="消息时间"
    )

    task: Mapped["Task"] = relationship(back_populates="messages")

    __table_args__ = (
        Index("idx_messages_task_round", "task_id", "round_number", "sequence"),
    )


class ToolCall(Base):
    """工具调用记录表（架构 §4.2.4）。

    记录每次 MCP 工具调用的参数、结果和耗时，用于 trace 和成功率统计（T-02）。
    """

    __tablename__ = "tool_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="MCP 工具名")
    arguments: Mapped[str] = mapped_column(Text, nullable=False, comment="调用参数（JSON）")
    result: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="工具返回结果")
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="SUCCESS", comment="SUCCESS/FAILED"
    )
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="调用耗时（毫秒）")
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="失败原因")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), comment="调用时间"
    )

    task: Mapped["Task"] = relationship(back_populates="tool_calls")

    __table_args__ = (
        Index("idx_tool_calls_task", "task_id"),
        Index("idx_tool_calls_name", "tool_name"),
    )
