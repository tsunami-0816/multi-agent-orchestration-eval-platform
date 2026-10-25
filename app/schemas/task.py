"""任务相关 Pydantic 模型（T8-2）。

定义任务接口的请求和响应数据结构。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class TaskCreateRequest(BaseModel):
    """创建任务请求（T8-3）。"""

    input: str = Field(..., min_length=1, max_length=10000, description="用户输入/任务描述")
    scenario: str = Field(default="orchestration", description="任务类型：orchestration/eval")
    callback_url: Optional[str] = Field(default=None, description="完成回调 URL")
    max_rounds: int = Field(default=5, ge=1, le=20, description="最大轮次")


class TaskResponse(BaseModel):
    """任务响应（T8-3）。"""

    id: str
    scenario: str
    input: str
    status: str
    result: Optional[str] = None
    error_message: Optional[str] = None
    trace_id: Optional[str] = None
    callback_url: Optional[str] = None
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class TaskListResponse(BaseModel):
    """任务列表响应（T8-3）。"""

    items: list[TaskResponse]
    total: int
    page: int
    page_size: int


class TaskCancelResponse(BaseModel):
    """取消任务响应（T8-5）。"""

    id: str
    status: str
    message: str


class TaskMessageResponse(BaseModel):
    """任务消息响应（T8-4，trace 接口）。"""

    id: int
    task_id: str
    role: str
    content: str
    node: Optional[str] = None
    sequence: int
    # 修复-OUT-002 改动原因：AgentMessage ORM 模型已有 agent_role/message_type/round_number 列，
    # messages 接口已手动返回这些字段，但 trace 接口通过 model_validate 转换时 Schema 缺少这些字段导致丢失
    agent_role: Optional[str] = None
    message_type: Optional[str] = None
    round_number: Optional[int] = None
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class TaskTraceResponse(BaseModel):
    """任务完整轨迹响应（T8-4）。"""

    task_id: str
    messages: list[TaskMessageResponse]
    total: int
    checkpoints: list[dict[str, Any]] = Field(default_factory=list, description="修复-BUG-024 检查点快照列表")


class TaskStatusResponse(BaseModel):
    """任务状态响应。"""

    id: str
    status: str
    progress: float = Field(description="进度百分比 0-100")
    completed_at: Optional[datetime] = None
