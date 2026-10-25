"""MCP Server 单元测试（修复-S01 覆盖率提升）。

覆盖 MCPServer 的请求处理、工具注册、JSON-RPC 响应构造等逻辑。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.mcp.registry import ToolRegistry
from app.mcp.server import (
    JSONRPC_INTERNAL_ERROR,
    JSONRPC_METHOD_NOT_FOUND,
    MCPServer,
)


class _FakeTool:
    """测试用假工具。"""

    def __init__(self, name: str = "fake_tool", should_fail: bool = False) -> None:
        self.name = name
        self.description = "A fake tool for testing"
        self.input_schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1},
            },
            "required": ["query"],
        }
        self._should_fail = should_fail

    def execute(self, arguments: dict[str, Any]) -> str:
        if self._should_fail:
            raise RuntimeError("tool execution failed")
        return f"result for {arguments.get('query', '')}"


class TestMCPServerInit:
    """测试 MCP Server 初始化与工具注册。"""

    def test_init_registers_default_tools(self) -> None:
        """测试初始化时自动注册 4 个默认工具。"""
        server = MCPServer()
        assert server.registry.has("web_search")
        assert server.registry.has("code_exec")
        assert server.registry.has("file_rw")
        assert server.registry.has("db_query")
        assert server._initialized is False

    def test_init_does_not_register_duplicates(self) -> None:
        """测试重复初始化不会重复注册工具。"""
        server1 = MCPServer()
        server2 = MCPServer()
        # 全局注册中心共享，两次初始化都不会报错
        assert server2.registry.has("web_search")


class TestMCPServerHandleRequest:
    """测试 JSON-RPC 请求处理。"""

    def test_initialize_request(self) -> None:
        """测试 initialize 请求返回服务器能力信息。"""
        server = MCPServer()
        resp = server.handle_request({"id": 1, "method": "initialize", "params": {}})
        assert resp is not None
        assert resp["jsonrpc"] == "2.0"
        assert resp["id"] == 1
        assert "protocolVersion" in resp["result"]
        assert "capabilities" in resp["result"]
        assert "serverInfo" in resp["result"]

    def test_tools_list_request(self) -> None:
        """测试 tools/list 请求返回工具列表。"""
        server = MCPServer()
        resp = server.handle_request({"id": 2, "method": "tools/list", "params": {}})
        assert resp is not None
        assert "tools" in resp["result"]
        assert len(resp["result"]["tools"]) >= 4

    def test_ping_request(self) -> None:
        """测试 ping 请求返回空结果。"""
        server = MCPServer()
        resp = server.handle_request({"id": 3, "method": "ping", "params": {}})
        assert resp is not None
        assert resp["result"] == {}

    def test_unknown_method_returns_error(self) -> None:
        """测试未知方法返回 METHOD_NOT_FOUND 错误。"""
        server = MCPServer()
        resp = server.handle_request({"id": 4, "method": "unknown/method", "params": {}})
        assert resp is not None
        assert "error" in resp
        assert resp["error"]["code"] == JSONRPC_METHOD_NOT_FOUND

    def test_notification_without_id_returns_none(self) -> None:
        """测试无 id 的通知请求返回 None。"""
        server = MCPServer()
        resp = server.handle_request({"method": "notifications/initialized", "params": {}})
        assert resp is None
        assert server._initialized is True

    def test_notification_unknown_method_no_error(self) -> None:
        """测试未知通知方法不报错。"""
        server = MCPServer()
        resp = server.handle_request({"method": "notifications/unknown", "params": {}})
        assert resp is None

    def test_internal_error_wrapped(self) -> None:
        """测试处理过程中异常被捕获为 INTERNAL_ERROR。"""
        server = MCPServer()

        # 让 _handle_tools_list 抛出异常
        original = server._handle_tools_list
        def _boom() -> dict[str, Any]:
            raise RuntimeError("unexpected")
        server._handle_tools_list = _boom  # type: ignore[method-assign]

        resp = server.handle_request({"id": 5, "method": "tools/list", "params": {}})
        assert resp is not None
        assert resp["error"]["code"] == JSONRPC_INTERNAL_ERROR
        assert "unexpected" in resp["error"]["message"]

        server._handle_tools_list = original  # type: ignore[method-assign]


class TestMCPServerToolsCall:
    """测试 tools/call 请求处理。"""

    def test_successful_tool_call(self) -> None:
        """测试工具调用成功返回结果。"""
        server = MCPServer()
        # 注册一个假工具到独立 registry
        server.registry = ToolRegistry()
        server.registry.register(_FakeTool())

        resp = server.handle_request({
            "id": 10,
            "method": "tools/call",
            "params": {"name": "fake_tool", "arguments": {"query": "hello"}},
        })
        assert resp is not None
        assert resp["result"]["isError"] is False
        assert "result for hello" in resp["result"]["content"][0]["text"]

    def test_tool_call_validation_error(self) -> None:
        """测试工具参数校验失败返回 isError=True。"""
        server = MCPServer()
        server.registry = ToolRegistry()
        server.registry.register(_FakeTool())

        resp = server.handle_request({
            "id": 11,
            "method": "tools/call",
            "params": {"name": "fake_tool", "arguments": {}},  # 缺少必填 query
        })
        assert resp is not None
        assert resp["result"]["isError"] is True

    def test_tool_call_not_found(self) -> None:
        """测试调用不存在的工具返回 isError=True。"""
        server = MCPServer()
        server.registry = ToolRegistry()

        resp = server.handle_request({
            "id": 12,
            "method": "tools/call",
            "params": {"name": "nonexistent", "arguments": {}},
        })
        assert resp is not None
        assert resp["result"]["isError"] is True

    def test_tool_call_execution_failure(self) -> None:
        """测试工具执行异常返回 isError=True。"""
        server = MCPServer()
        server.registry = ToolRegistry()
        server.registry.register(_FakeTool(should_fail=True))

        resp = server.handle_request({
            "id": 13,
            "method": "tools/call",
            "params": {"name": "fake_tool", "arguments": {"query": "x"}},
        })
        assert resp is not None
        assert resp["result"]["isError"] is True
        assert "tool execution failed" in resp["result"]["content"][0]["text"]


class TestMCPServerResponseHelpers:
    """测试响应构造辅助方法。"""

    def test_success_response_format(self) -> None:
        """测试成功响应格式。"""
        resp = MCPServer._success_response(1, {"key": "value"})
        assert resp["jsonrpc"] == "2.0"
        assert resp["id"] == 1
        assert resp["result"] == {"key": "value"}

    def test_error_response_format(self) -> None:
        """测试错误响应格式。"""
        resp = MCPServer._error_response(2, -32601, "Method not found")
        assert resp["jsonrpc"] == "2.0"
        assert resp["id"] == 2
        assert resp["error"]["code"] == -32601
        assert resp["error"]["message"] == "Method not found"
