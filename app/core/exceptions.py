"""统一异常体系（T0-4）。

定义 AppException 基类 + 全部错误码（10000~50099 共 5 模块），
FastAPI 全局异常处理器返回统一错误信封。

错误码规范（架构 §5.2）：
- 5 位数字，前 2 位为模块，后 3 位为具体错误
- 10000-10999: 通用/系统错误
- 20000-20999: 任务模块错误
- 30000-30999: 评测模块错误
- 40000-40999: 工具/MCP 模块错误
- 50000-50999: LLM 网关错误
"""

from __future__ import annotations

from enum import IntEnum
from typing import Any


class ErrorCode(IntEnum):
    """全部错误码枚举（架构 §5.2）。"""

    # === 通用/系统错误 10000-10999 ===
    INTERNAL_ERROR = 10001
    VALIDATION_ERROR = 10002
    RESOURCE_NOT_FOUND = 10003
    RATE_LIMITED = 10004
    UNAUTHORIZED = 10005

    # === 任务模块错误 20000-20999 ===
    TASK_INPUT_EMPTY = 20001
    TASK_NOT_FOUND = 20002
    TASK_STATUS_INVALID_OPERATION = 20003
    TASK_EXECUTION_FAILED = 20004
    TASK_CANCEL_TIMEOUT = 20005

    # === 评测模块错误 30000-30999 ===
    EVAL_CASES_EMPTY = 30001
    EVAL_RUN_NOT_FOUND = 30002
    EVAL_REPORT_NOT_READY = 30003

    # === 工具/MCP 模块错误 40000-40999 ===
    TOOL_NOT_FOUND = 40001
    TOOL_VALIDATION_ERROR = 40002
    TOOL_TIMEOUT = 40003
    TOOL_EXECUTION_ERROR = 40004

    # === LLM 网关错误 50000-50999 ===
    LLM_API_ERROR = 50001
    LLM_TIMEOUT = 50002
    LLM_BUDGET_EXCEEDED = 50003
    LLM_INVALID_RESPONSE = 50004


# 错误码到 HTTP 状态码的映射
_ERROR_HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.VALIDATION_ERROR: 400,
    ErrorCode.RESOURCE_NOT_FOUND: 404,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.TASK_INPUT_EMPTY: 400,
    ErrorCode.TASK_NOT_FOUND: 404,
    ErrorCode.TASK_STATUS_INVALID_OPERATION: 409,
    ErrorCode.TASK_EXECUTION_FAILED: 500,
    ErrorCode.TASK_CANCEL_TIMEOUT: 200,
    ErrorCode.EVAL_CASES_EMPTY: 400,
    ErrorCode.EVAL_RUN_NOT_FOUND: 404,
    ErrorCode.EVAL_REPORT_NOT_READY: 404,
    ErrorCode.TOOL_NOT_FOUND: 400,
    ErrorCode.TOOL_VALIDATION_ERROR: 400,
    ErrorCode.TOOL_TIMEOUT: 504,
    ErrorCode.TOOL_EXECUTION_ERROR: 502,
    ErrorCode.LLM_API_ERROR: 502,
    ErrorCode.LLM_TIMEOUT: 504,
    ErrorCode.LLM_BUDGET_EXCEEDED: 429,
    ErrorCode.LLM_INVALID_RESPONSE: 502,
}

# 错误码到默认消息的映射
_ERROR_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.INTERNAL_ERROR: "内部服务器错误",
    ErrorCode.VALIDATION_ERROR: "请求参数校验失败",
    ErrorCode.RESOURCE_NOT_FOUND: "资源不存在",
    ErrorCode.RATE_LIMITED: "请求过于频繁",
    ErrorCode.UNAUTHORIZED: "未授权访问",
    ErrorCode.TASK_INPUT_EMPTY: "任务输入不能为空",
    ErrorCode.TASK_NOT_FOUND: "任务不存在",
    ErrorCode.TASK_STATUS_INVALID_OPERATION: "任务状态不允许该操作",
    ErrorCode.TASK_EXECUTION_FAILED: "任务执行失败",
    ErrorCode.TASK_CANCEL_TIMEOUT: "任务取消超时，已强制终止",
    ErrorCode.EVAL_CASES_EMPTY: "评测用例库为空",
    ErrorCode.EVAL_RUN_NOT_FOUND: "评测运行不存在",
    ErrorCode.EVAL_REPORT_NOT_READY: "报告尚未生成",
    ErrorCode.TOOL_NOT_FOUND: "MCP 工具不存在",
    ErrorCode.TOOL_VALIDATION_ERROR: "工具参数校验失败",
    ErrorCode.TOOL_TIMEOUT: "工具执行超时",
    ErrorCode.TOOL_EXECUTION_ERROR: "工具执行异常",
    ErrorCode.LLM_API_ERROR: "LLM API 调用失败",
    ErrorCode.LLM_TIMEOUT: "LLM API 超时",
    ErrorCode.LLM_BUDGET_EXCEEDED: "LLM API 配额不足",
    ErrorCode.LLM_INVALID_RESPONSE: "LLM 响应格式异常",
}


class AppException(Exception):
    """应用异常基类。

    所有业务异常均继承此类，包含错误码、消息、HTTP 状态码和详情。
    FastAPI 全局异常处理器将其转换为统一错误信封响应。

    Attributes:
        code: 错误码（ErrorCode 枚举）
        message: 错误描述
        http_status: 对应的 HTTP 状态码
        details: 额外详情（如字段级校验错误）
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """初始化应用异常。

        Args:
            code: 错误码枚举
            message: 自定义错误消息，为 None 时使用默认消息
            details: 额外详情字典
        """
        self.code = code
        self.message = message or _ERROR_MESSAGES.get(code, "未知错误")
        self.http_status = _ERROR_HTTP_STATUS.get(code, 500)
        self.details = details or {}
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        """转换为错误响应字典（统一错误信封）。

        Returns:
            包含 code/message/details 的字典。
        """
        return {
            "code": int(self.code),
            "message": self.message,
            "details": self.details,
        }


# === 便捷异常类 ===


class ValidationException(AppException):
    """请求参数校验失败异常。"""

    def __init__(self, message: str | None = None, details: dict[str, Any] | None = None) -> None:
        super().__init__(ErrorCode.VALIDATION_ERROR, message, details)


class NotFoundException(AppException):
    """资源不存在异常。"""

    def __init__(self, message: str | None = None, details: dict[str, Any] | None = None) -> None:
        super().__init__(ErrorCode.RESOURCE_NOT_FOUND, message, details)


class TaskNotFoundException(AppException):
    """任务不存在异常。"""

    def __init__(self, task_id: str) -> None:
        super().__init__(ErrorCode.TASK_NOT_FOUND, f"任务 {task_id} 不存在", {"task_id": task_id})


class TaskStatusException(AppException):
    """任务状态不允许操作异常。"""

    def __init__(self, task_id: str, current_status: str, operation: str) -> None:
        super().__init__(
            ErrorCode.TASK_STATUS_INVALID_OPERATION,
            f"任务 {task_id} 当前状态 {current_status} 不允许操作 {operation}",
            {"task_id": task_id, "current_status": current_status, "operation": operation},
        )


class LLMBudgetExceededException(AppException):
    """LLM 预算超限异常（P1-9）。"""

    def __init__(self, current_cost: float, limit: float) -> None:
        super().__init__(
            ErrorCode.LLM_BUDGET_EXCEEDED,
            f"LLM 当日费用 ¥{current_cost:.2f} 已超过预算 ¥{limit:.2f}",
            {"current_cost": current_cost, "limit": limit},
        )
