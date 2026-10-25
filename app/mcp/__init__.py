"""MCP 工具服务层（M3）。

提供 MCP Server/Client 和 4 个工具适配器：
- web_search: DuckDuckGo 网络搜索
- code_exec: 隔离 Python 代码执行
- file_rw: 工作目录文件读写
- db_query: 只读数据库查询
"""

from app.mcp.client import MCPSession, create_session
from app.mcp.registry import ToolRegistry, get_registry, register_tool, validate_arguments

__all__ = [
    "MCPSession",
    "create_session",
    "ToolRegistry",
    "get_registry",
    "register_tool",
    "validate_arguments",
]
