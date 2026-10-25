"""MCP Server 入口（T3-2）。

基于 JSON-RPC 2.0 over stdio 实现 MCP Server：
- 注册全部工具（web_search/code_exec/file_rw/db_query）
- 处理 tools/list 和 tools/call JSON-RPC 请求
- 禁止 print 到 stdout（stdio 被 MCP 占用，P1-2）

架构决策（P1-2）：使用轻量级 JSON-RPC 实现，
不依赖 MCP SDK 的具体 API 版本，确保稳定性。
通过 stdin/stdout 进行 JSON-RPC 通信。

启动方式：python -m app.mcp.server
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any

from app.core.logging import get_logger
from app.mcp.registry import get_registry, validate_arguments
from app.mcp.tools.code_exec import CodeExecTool
from app.mcp.tools.db_query import DBQueryTool
from app.mcp.tools.file_rw import FileRWTool
from app.mcp.tools.web_search import WebSearchTool

logger = get_logger(__name__)

# JSON-RPC 错误码
JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603


class MCPServer:
    """MCP Server（T3-2）。

    通过 stdin/stdout 进行 JSON-RPC 2.0 通信，
    提供 tools/list 和 tools/call 方法。
    """

    def __init__(self) -> None:
        """初始化 Server，注册全部工具。"""
        self.registry = get_registry()
        self._register_default_tools()
        self._initialized = False

    def _register_default_tools(self) -> None:
        """注册全部默认工具（T-01 ≥4 MCP 工具）。"""
        tools = [
            WebSearchTool(),
            CodeExecTool(),
            FileRWTool(),
            DBQueryTool(),
        ]
        for tool in tools:
            if not self.registry.has(tool.name):
                self.registry.register(tool)

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """处理 JSON-RPC 请求。

        Args:
            request: JSON-RPC 请求字典

        Returns:
            JSON-RPC 响应字典，通知（无 id）时返回 None
        """
        request_id = request.get("id")
        method = request.get("method", "")
        params = request.get("params", {})

        # 通知（无 id）不需要响应
        if request_id is None:
            self._handle_notification(method, params)
            return None

        try:
            if method == "initialize":
                result = self._handle_initialize(params)
            elif method == "tools/list":
                result = self._handle_tools_list()
            elif method == "tools/call":
                result = self._handle_tools_call(params)
            elif method == "ping":
                result = {}
            else:
                return self._error_response(request_id, JSONRPC_METHOD_NOT_FOUND, f"Method not found: {method}")

            return self._success_response(request_id, result)

        except Exception as e:
            logger.error("mcp_request_handler_error", method=method, error=str(e))
            return self._error_response(request_id, JSONRPC_INTERNAL_ERROR, str(e))

    def _handle_notification(self, method: str, params: dict[str, Any]) -> None:
        """处理 JSON-RPC 通知（无响应）。

        Args:
            method: 通知方法名
            params: 通知参数
        """
        if method == "notifications/initialized":
            self._initialized = True
            logger.info("mcp_server_initialized")

    def _handle_initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        """处理 initialize 请求。

        Args:
            params: 初始化参数

        Returns:
            服务器能力信息
        """
        return {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {"listChanged": False},
            },
            "serverInfo": {
                "name": "multi-agent-eval-mcp-server",
                "version": "0.1.0",
            },
        }

    def _handle_tools_list(self) -> dict[str, Any]:
        """处理 tools/list 请求。

        Returns:
            工具列表
        """
        tools = self.registry.list_tools()
        return {"tools": tools}

    def _handle_tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        """处理 tools/call 请求。

        Args:
            params: 调用参数，含 name 和 arguments

        Returns:
            工具执行结果
        """
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        try:
            tool = self.registry.get(tool_name)
            # JSON Schema 参数校验（T3-8，T-04 100% 覆盖）
            validate_arguments(tool, arguments)
            # 执行工具
            result = tool.execute(arguments)
            return {
                "content": [{"type": "text", "text": result}],
                "isError": False,
            }
        except Exception as e:
            logger.error("mcp_tool_call_error", tool=tool_name, error=str(e))
            return {
                "content": [{"type": "text", "text": f"工具执行失败: {e}"}],
                "isError": True,
            }

    @staticmethod
    def _success_response(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        """构造成功响应。"""
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error_response(request_id: Any, code: int, message: str) -> dict[str, Any]:
        """构造错误响应。"""
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }

    def run(self) -> None:
        """运行 Server 主循环（从 stdin 读取请求，写入 stdout）。

        注意：禁止使用 print()，必须通过 sys.stdout.write 写入 JSON-RPC 响应，
        因为 stdio 被 MCP 协议占用（P1-2）。
        """
        logger.info("mcp_server_starting")

        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue

            try:
                request = json.loads(line)
            except json.JSONDecodeError as e:
                logger.error("mcp_parse_error", error=str(e))
                error_resp = self._error_response(None, JSONRPC_PARSE_ERROR, "Parse error")
                sys.stdout.write(json.dumps(error_resp) + "\n")
                sys.stdout.flush()
                continue

            response = self.handle_request(request)
            if response is not None:
                sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                sys.stdout.flush()


def main() -> None:
    """MCP Server 入口函数。

    MCP 协议要求 stdout 只能走 JSON-RPC 消息（P1-2），所有日志必须写到 stderr，
    否则子进程一启动 structlog 的 PrintLoggerFactory 就会把日志打到 stdout，
    client 从 stdout 读第一行时拿到日志 JSON 而非 initialize 响应，导致
    "Extra data: line 1 column 5" 解析错误。
    """
    import sys as _sys
    import structlog as _structlog

    # 把 structlog 输出重定向到 stderr，stdout 留给 JSON-RPC
    _structlog.configure(
        processors=[
            _structlog.processors.add_log_level,
            _structlog.processors.TimeStamper(fmt="iso", utc=True),
            _structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        logger_factory=_structlog.PrintLoggerFactory(file=_sys.stderr),
        cache_logger_on_first_use=False,
    )

    server = MCPServer()
    server.run()


if __name__ == "__main__":
    main()
