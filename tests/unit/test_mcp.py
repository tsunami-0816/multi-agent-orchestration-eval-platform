"""MCP 工具层单元测试（M12）。

覆盖：工具注册中心、参数校验、4个工具适配器。
"""

from __future__ import annotations

import pytest

from app.mcp.registry import ToolRegistry, validate_arguments
from app.mcp.tools.code_exec import CodeExecTool
from app.mcp.tools.file_rw import FileRWTool
from app.mcp.tools.web_search import WebSearchTool


class TestToolRegistry:
    """工具注册中心测试。"""

    def test_register_and_get(self) -> None:
        """测试工具注册和获取。"""
        registry = ToolRegistry()
        tool = WebSearchTool()
        registry.register(tool)
        assert registry.has("web_search")
        retrieved = registry.get("web_search")
        assert retrieved.name == "web_search"

    def test_duplicate_register_raises(self) -> None:
        """测试重复注册抛出异常。"""
        registry = ToolRegistry()
        tool = WebSearchTool()
        registry.register(tool)
        with pytest.raises(ValueError):
            registry.register(tool)

    def test_get_nonexistent_raises(self) -> None:
        """测试获取不存在的工具抛出异常。"""
        from app.core.exceptions import AppException

        registry = ToolRegistry()
        with pytest.raises(AppException):
            registry.get("nonexistent_tool")

    def test_list_tools(self) -> None:
        """测试列出全部工具。"""
        registry = ToolRegistry()
        registry.register(WebSearchTool())
        registry.register(CodeExecTool())
        tools = registry.list_tools()
        assert len(tools) == 2
        names = {t["name"] for t in tools}
        assert "web_search" in names
        assert "code_exec" in names

    def test_unregister(self) -> None:
        """测试注销工具。"""
        registry = ToolRegistry()
        tool = WebSearchTool()
        registry.register(tool)
        assert registry.has("web_search")
        registry.unregister("web_search")
        assert not registry.has("web_search")


class TestArgumentValidation:
    """JSON Schema 参数校验测试（T3-8）。"""

    def test_valid_arguments_pass(self) -> None:
        """测试合法参数通过校验。"""
        tool = WebSearchTool()
        result = validate_arguments(tool, {"query": "test"})
        assert result["query"] == "test"

    def test_missing_required_raises(self) -> None:
        """测试缺少必填参数抛出异常。"""
        from app.core.exceptions import AppException

        tool = WebSearchTool()
        with pytest.raises(AppException):
            validate_arguments(tool, {})

    def test_wrong_type_raises(self) -> None:
        """测试类型错误抛出异常。"""
        from app.core.exceptions import AppException

        tool = WebSearchTool()
        with pytest.raises(AppException):
            validate_arguments(tool, {"query": 123})

    def test_enum_validation(self) -> None:
        """测试枚举值校验。"""
        from app.core.exceptions import AppException

        tool = FileRWTool()
        with pytest.raises(AppException):
            validate_arguments(tool, {"operation": "invalid_op", "path": "test.txt"})


class TestCodeExecTool:
    """代码执行工具测试（P1-8）。"""

    def test_dangerous_code_blocked(self) -> None:
        """测试危险代码被拦截。"""
        from app.core.exceptions import AppException

        tool = CodeExecTool()
        with pytest.raises(AppException):
            tool.execute({"code": "import os\nos.remove('/tmp/test')"})

    def test_simple_code_execution(self) -> None:
        """测试简单代码执行。"""
        tool = CodeExecTool()
        result = tool.execute({"code": "print('hello world')"})
        assert "hello world" in result

    def test_code_timeout(self) -> None:
        """测试代码超时。"""
        from app.core.exceptions import AppException

        tool = CodeExecTool()
        with pytest.raises(AppException):
            tool.execute({"code": "import time\ntime.sleep(20)", "timeout": 1})
