"""统一异常体系单元测试（T0-4）。

测试错误码定义、AppException 基类、便捷异常类、错误响应转换。
"""

from __future__ import annotations

import pytest

from app.core.exceptions import (
    AppException,
    ErrorCode,
    LLMBudgetExceededException,
    NotFoundException,
    TaskNotFoundException,
    TaskStatusException,
    ValidationException,
)


class TestErrorCode:
    """错误码枚举测试。"""

    def test_error_code_ranges(self) -> None:
        """测试错误码范围划分。"""
        # 通用错误 10000-10999
        assert 10000 <= ErrorCode.INTERNAL_ERROR <= 10999
        assert 10000 <= ErrorCode.VALIDATION_ERROR <= 10999
        # 任务错误 20000-20999
        assert 20000 <= ErrorCode.TASK_NOT_FOUND <= 20999
        assert 20000 <= ErrorCode.TASK_STATUS_INVALID_OPERATION <= 20999
        # 评测错误 30000-30999
        assert 30000 <= ErrorCode.EVAL_RUN_NOT_FOUND <= 30999
        # 工具错误 40000-40999
        assert 40000 <= ErrorCode.TOOL_NOT_FOUND <= 40999
        # LLM 错误 50000-50999
        assert 50000 <= ErrorCode.LLM_API_ERROR <= 50999
        assert 50000 <= ErrorCode.LLM_BUDGET_EXCEEDED <= 50999

    def test_success_code_is_zero(self) -> None:
        """测试成功码为 0。"""
        # 成功不在 ErrorCode 枚举中，直接验证
        assert 0 not in [e.value for e in ErrorCode]


class TestAppException:
    """AppException 基类测试。"""

    def test_create_with_default_message(self) -> None:
        """测试使用默认消息创建异常。"""
        exc = AppException(ErrorCode.TASK_NOT_FOUND)
        assert exc.code == ErrorCode.TASK_NOT_FOUND
        assert "任务不存在" in exc.message
        assert exc.http_status == 404

    def test_create_with_custom_message(self) -> None:
        """测试使用自定义消息创建异常。"""
        exc = AppException(ErrorCode.VALIDATION_ERROR, message="自定义错误")
        assert exc.message == "自定义错误"

    def test_to_dict(self) -> None:
        """测试转换为字典。"""
        exc = AppException(ErrorCode.LLM_TIMEOUT, details={"model": "test"})
        d = exc.to_dict()
        assert d["code"] == 50002
        assert "超时" in d["message"]
        assert d["details"]["model"] == "test"

    def test_http_status_mapping(self) -> None:
        """测试错误码到 HTTP 状态码的映射。"""
        assert AppException(ErrorCode.INTERNAL_ERROR).http_status == 500
        assert AppException(ErrorCode.VALIDATION_ERROR).http_status == 400
        assert AppException(ErrorCode.RESOURCE_NOT_FOUND).http_status == 404
        assert AppException(ErrorCode.UNAUTHORIZED).http_status == 401
        assert AppException(ErrorCode.RATE_LIMITED).http_status == 429
        assert AppException(ErrorCode.TASK_STATUS_INVALID_OPERATION).http_status == 409
        assert AppException(ErrorCode.LLM_TIMEOUT).http_status == 504
        assert AppException(ErrorCode.LLM_BUDGET_EXCEEDED).http_status == 429


class TestConvenienceExceptions:
    """便捷异常类测试。"""

    def test_validation_exception(self) -> None:
        exc = ValidationException("字段缺失")
        assert exc.code == ErrorCode.VALIDATION_ERROR
        assert exc.message == "字段缺失"

    def test_not_found_exception(self) -> None:
        exc = NotFoundException()
        assert exc.code == ErrorCode.RESOURCE_NOT_FOUND

    def test_task_not_found_exception(self) -> None:
        exc = TaskNotFoundException("task-123")
        assert exc.code == ErrorCode.TASK_NOT_FOUND
        assert exc.details["task_id"] == "task-123"

    def test_task_status_exception(self) -> None:
        exc = TaskStatusException("task-123", "COMPLETED", "cancel")
        assert exc.code == ErrorCode.TASK_STATUS_INVALID_OPERATION
        assert exc.details["current_status"] == "COMPLETED"
        assert exc.details["operation"] == "cancel"

    def test_llm_budget_exceeded_exception(self) -> None:
        exc = LLMBudgetExceededException(210.5, 200.0)
        assert exc.code == ErrorCode.LLM_BUDGET_EXCEEDED
        assert exc.details["current_cost"] == 210.5
        assert exc.details["limit"] == 200.0
