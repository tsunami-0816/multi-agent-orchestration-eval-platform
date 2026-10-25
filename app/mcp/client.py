"""MCP Client 封装（T3-3，P1-2 修正）。

任务级 Session 生命周期管理：
- 每次 Executor 节点创建新 Session，节点出口 finally 关闭
- 异常退出时子进程被 terminate/kill 不泄漏
- list_tools() 结果缓存
- 禁止全局单例（P1-2：避免多任务串话）
- 禁止 print 到 stdout（stdio 被 MCP 占用，P1-2）

架构决策（P1-2）：Session 为任务级，非全局单例，
避免 Celery 多进程下多任务串话和 Session 泄漏。
通过 try/finally + terminate/kill 兜底确保子进程不泄漏。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from typing import Any, Optional

from app.config import get_config
from app.core.exceptions import AppException, ErrorCode
from app.core.logging import get_logger

logger = get_logger(__name__)


class MCPSession:
    """MCP 任务级 Session（T3-3，P1-2）。

    管理一个 MCP Server 子进程的生命周期，通过 stdin/stdout 进行 JSON-RPC 通信。
    使用完毕后必须调用 close()，推荐使用 async with 上下文管理器。

    注意：这是同步实现（MCP stdio 为同步协议），
    在异步代码中使用时应通过 asyncio.to_thread 包裹。
    """

    def __init__(self, server_command: Optional[str] = None, timeout: int = 30) -> None:
        """初始化 Session（不启动子进程，需调用 start() 或使用上下文管理器）。

        Args:
            server_command: MCP Server 启动命令，默认从配置读取
            timeout: 单次请求超时（秒）
        """
        config = get_config()
        self.server_command = server_command or getattr(config, "mcp_server_command", "python -m app.mcp.server")
        self.timeout = timeout
        self.session_id = uuid.uuid4().hex[:8]
        self._process: Optional[subprocess.Popen] = None
        self._request_id = 0
        self._lock = threading.Lock()
        self._tools_cache: Optional[list[dict[str, Any]]] = None
        self._closed = False

    def start(self) -> None:
        """启动 MCP Server 子进程。

        Raises:
            AppException(TOOL_EXECUTION_ERROR): 子进程启动失败时抛出
        """
        if self._process is not None:
            return

        try:
            # 启动子进程，使用当前 Python 解释器
            cmd = self.server_command.split()
            # 如果命令以 python 开头，使用当前解释器
            if cmd[0] in ("python", "python3"):
                cmd[0] = sys.executable

            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
            logger.info("mcp_session_started", session_id=self.session_id, pid=self._process.pid)

            # 发送 initialize 请求
            self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "multi-agent-eval-client", "version": "0.1.0"},
            })

            # 发送 initialized 通知
            self._send_notification("notifications/initialized", {})

        except Exception as e:
            logger.error("mcp_session_start_failed", session_id=self.session_id, error=str(e))
            raise AppException(
                ErrorCode.TOOL_EXECUTION_ERROR,
                f"MCP Server 启动失败: {e}",
            ) from e

    def close(self) -> None:
        """关闭 Session，终止子进程（P1-2：try/finally + terminate/kill 兜底）。

        关闭流程：
        1. 尝试发送 shutdown 通知
        2. terminate 子进程（优雅退出）
        3. 等待 3 秒，若仍存活则 kill（强制终止）
        """
        if self._closed:
            return
        self._closed = True

        if self._process is None:
            return

        pid = self._process.pid
        try:
            # 尝试发送 shutdown 通知（忽略错误）
            try:
                self._send_notification("notifications/shutdown", {})
            except Exception:
                pass

            # 关闭 stdin，让 Server 自然退出
            try:
                if self._process.stdin:
                    self._process.stdin.close()
            except Exception:
                pass

            # 等待进程退出（最多 3 秒）
            try:
                self._process.wait(timeout=3)
                logger.info("mcp_session_closed_gracefully", session_id=self.session_id, pid=pid)
                return
            except subprocess.TimeoutExpired:
                pass

            # 优雅退出超时，terminate
            try:
                self._process.terminate()
                self._process.wait(timeout=2)
                logger.info("mcp_session_terminated", session_id=self.session_id, pid=pid)
                return
            except (subprocess.TimeoutExpired, Exception):
                pass

            # 最后手段：kill
            try:
                self._process.kill()
                self._process.wait(timeout=2)
                logger.warning("mcp_session_killed", session_id=self.session_id, pid=pid)
            except Exception as e:
                logger.error("mcp_session_kill_failed", session_id=self.session_id, pid=pid, error=str(e))

        finally:
            # 清理管道
            try:
                if self._process.stdout:
                    self._process.stdout.close()
            except Exception:
                pass
            try:
                if self._process.stderr:
                    self._process.stderr.close()
            except Exception:
                pass
            self._process = None

    def __enter__(self) -> "MCPSession":
        """上下文管理器入口。"""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """上下文管理器出口，确保 Session 关闭（P1-2）。"""
        self.close()

    def list_tools(self, use_cache: bool = True) -> list[dict[str, Any]]:
        """列出全部已注册工具。

        Args:
            use_cache: 是否使用缓存（默认 True）

        Returns:
            工具元信息列表
        """
        if use_cache and self._tools_cache is not None:
            return self._tools_cache

        result = self._send_request("tools/list", {})
        tools = result.get("tools", [])
        self._tools_cache = tools
        return tools

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """调用 MCP 工具。

        Args:
            tool_name: 工具名称
            arguments: 工具参数

        Returns:
            工具执行结果文本

        Raises:
            AppException(TOOL_NOT_FOUND): 工具不存在时抛出
            AppException(TOOL_TIMEOUT): 调用超时时抛出
            AppException(TOOL_EXECUTION_ERROR): 执行失败时抛出
        """
        result = self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        })

        # 检查是否为错误结果
        if result.get("isError", False):
            content = result.get("content", [])
            error_text = content[0].get("text", "未知错误") if content else "未知错误"
            logger.error("mcp_tool_call_returned_error", tool=tool_name, error=error_text)
            raise AppException(
                ErrorCode.TOOL_EXECUTION_ERROR,
                f"工具 {tool_name} 执行失败: {error_text}",
            )

        # 提取文本结果
        content = result.get("content", [])
        if content and content[0].get("type") == "text":
            return content[0]["text"]
        return str(result)

    def _send_request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """发送 JSON-RPC 请求并等待响应。

        Args:
            method: 请求方法名
            params: 请求参数

        Returns:
            响应结果字典

        Raises:
            AppException(TOOL_TIMEOUT): 请求超时时抛出
            AppException(TOOL_EXECUTION_ERROR): 通信错误时抛出
        """
        if self._process is None or self._closed:
            raise AppException(ErrorCode.TOOL_EXECUTION_ERROR, "MCP Session 未启动或已关闭")

        with self._lock:
            self._request_id += 1
            request_id = self._request_id

        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }

        try:
            # 写入请求
            request_json = json.dumps(request, ensure_ascii=False) + "\n"
            self._process.stdin.write(request_json)
            self._process.stdin.flush()

            # 读取响应（带超时）
            response_line = self._read_response_with_timeout(request_id)
            if response_line is None:
                raise AppException(
                    ErrorCode.TOOL_TIMEOUT,
                    f"MCP 请求超时（{self.timeout}s）: {method}",
                )

            response = json.loads(response_line)

            # 检查 JSON-RPC 错误
            if "error" in response:
                error = response["error"]
                raise AppException(
                    ErrorCode.TOOL_EXECUTION_ERROR,
                    f"MCP 错误: {error.get('message', '未知错误')}",
                    details={"code": error.get("code")},
                )

            return response.get("result", {})

        except (BrokenPipeError, OSError, ValueError) as e:
            logger.error("mcp_communication_error", method=method, error=str(e))
            raise AppException(
                ErrorCode.TOOL_EXECUTION_ERROR,
                f"MCP 通信错误: {e}",
            ) from e

    def _read_response_with_timeout(self, expected_id: int) -> Optional[str]:
        """带超时地读取响应行。

        Args:
            expected_id: 期望的请求 ID

        Returns:
            响应行文本，超时返回 None
        """
        start_time = time.time()
        while time.time() - start_time < self.timeout:
            # 检查进程是否存活
            if self._process.poll() is not None:
                logger.error("mcp_process_died", session_id=self.session_id, returncode=self._process.returncode)
                return None

            # 非阻塞读取一行
            try:
                import select
                ready, _, _ = select.select([self._process.stdout], [], [], 0.1)
                if ready:
                    line = self._process.stdout.readline()
                    if line:
                        line = line.strip()
                        if line:
                            # 验证请求 ID 匹配
                            try:
                                resp = json.loads(line)
                                if resp.get("id") == expected_id:
                                    return line
                                # ID 不匹配，继续读取（可能是之前的响应）
                            except json.JSONDecodeError:
                                continue
            except (ImportError, OSError):
                # Windows 不支持 select on pipe，使用阻塞读取带超时
                try:
                    import threading
                    result_container: dict[str, Optional[str]] = {}

                    def _read_line() -> None:
                        try:
                            result_container["line"] = self._process.stdout.readline()
                        except Exception:
                            result_container["line"] = None

                    reader_thread = threading.Thread(target=_read_line, daemon=True)
                    reader_thread.start()
                    reader_thread.join(timeout=self.timeout)

                    if reader_thread.is_alive():
                        return None
                    line = result_container.get("line")
                    if line and line.strip():
                        return line.strip()
                except Exception:
                    return None

        return None

    def _send_notification(self, method: str, params: dict[str, Any]) -> None:
        """发送 JSON-RPC 通知（无响应）。

        Args:
            method: 通知方法名
            params: 通知参数
        """
        if self._process is None or self._closed:
            return

        notification = {"jsonrpc": "2.0", "method": method, "params": params}
        try:
            self._process.stdin.write(json.dumps(notification, ensure_ascii=False) + "\n")
            self._process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

    @property
    def is_alive(self) -> bool:
        """检查子进程是否存活。"""
        return self._process is not None and self._process.poll() is None and not self._closed


def create_session(server_command: Optional[str] = None, timeout: int = 30) -> MCPSession:
    """创建新的 MCP Session（工厂函数）。

    注意：每次调用创建新 Session，禁止全局单例（P1-2）。

    Args:
        server_command: MCP Server 启动命令
        timeout: 请求超时（秒）

    Returns:
        新的 MCPSession 实例（未启动，需调用 start() 或使用 with）
    """
    return MCPSession(server_command=server_command, timeout=timeout)
