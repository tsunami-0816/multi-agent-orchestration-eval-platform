"""统一请求/响应模型（T8-1/T8-2）。

定义 API 层通用的 Pydantic 模型：
- ApiResponse：统一成功响应信封（code/message/data/request_id/timestamp）
- PaginatedResponse：分页响应格式（items/total/page/page_size/total_pages）
- ErrorResponse：错误响应格式

所有接口返回统一信封格式（架构 §5.1）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Generic, Optional, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """统一成功响应信封（架构 §5.1.1）。

    Attributes:
        code: 业务状态码，0 表示成功
        message: 状态描述
        data: 业务数据
        request_id: 请求唯一标识（trace_id）
        timestamp: 响应时间（ISO 8601 UTC）
    """

    code: int = Field(default=0, description="业务状态码，0 表示成功")
    message: str = Field(default="success", description="状态描述")
    data: Optional[T] = Field(default=None, description="业务数据")
    request_id: str = Field(default="", description="请求唯一标识")
    timestamp: str = Field(default="", description="响应时间（ISO 8601 UTC）")

    @classmethod
    def success(
        cls,
        data: Any = None,
        request_id: str = "",
        message: str = "success",
    ) -> "ApiResponse[Any]":
        """创建成功响应。

        Args:
            data: 业务数据
            request_id: 请求 ID
            message: 状态描述

        Returns:
            ApiResponse 实例
        """
        return cls(
            code=0,
            message=message,
            data=data,
            request_id=request_id,
            timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        )


class PaginationMeta(BaseModel):
    """分页元数据。

    Attributes:
        total: 总记录数
        page: 当前页码（从 1 开始）
        page_size: 每页数量
        total_pages: 总页数
    """

    total: int = Field(description="总记录数")
    page: int = Field(description="当前页码")
    page_size: int = Field(description="每页数量")
    total_pages: int = Field(description="总页数")


class PaginatedData(BaseModel, Generic[T]):
    """分页数据结构。

    Attributes:
        items: 数据列表
        pagination: 分页元数据
    """

    items: list[T] = Field(default_factory=list, description="数据列表")
    pagination: PaginationMeta = Field(description="分页元数据")


class ErrorDetails(BaseModel):
    """错误详情。

    Attributes:
        field: 出错字段名（参数校验错误时）
        reason: 错误原因
    """

    field: Optional[str] = Field(default=None, description="出错字段名")
    reason: Optional[str] = Field(default=None, description="错误原因")


class ErrorResponse(BaseModel):
    """统一错误响应信封（架构 §5.1.3）。

    Attributes:
        code: 错误码（非 0）
        message: 错误描述
        data: 始终为 null
        request_id: 请求唯一标识
        timestamp: 响应时间
        details: 额外详情
    """

    code: int = Field(description="错误码")
    message: str = Field(description="错误描述")
    data: Any = Field(default=None, description="始终为 null")
    request_id: str = Field(default="", description="请求唯一标识")
    timestamp: str = Field(default="", description="响应时间")
    details: Optional[dict[str, Any]] = Field(default=None, description="额外详情")

    @classmethod
    def from_exception(
        cls,
        code: int,
        message: str,
        request_id: str = "",
        details: Optional[dict[str, Any]] = None,
    ) -> "ErrorResponse":
        """从异常创建错误响应。

        Args:
            code: 错误码
            message: 错误消息
            request_id: 请求 ID
            details: 额外详情

        Returns:
            ErrorResponse 实例
        """
        return cls(
            code=code,
            message=message,
            data=None,
            request_id=request_id,
            timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            details=details,
        )


class HealthResponse(BaseModel):
    """健康检查响应（架构 §5.4.3）。

    Attributes:
        status: 整体状态（ok/degraded/down）
        version: 应用版本
        db: 数据库状态
        redis: Redis 状态
        llm: LLM 连通性状态
    """

    status: str = Field(description="整体状态：ok/degraded/down")
    version: str = Field(description="应用版本")
    db: str = Field(description="数据库状态：ok/error")
    redis: str = Field(description="Redis 状态：ok/error/skipped")
    llm: str = Field(description="LLM 状态：ok/warning/error/skipped")
