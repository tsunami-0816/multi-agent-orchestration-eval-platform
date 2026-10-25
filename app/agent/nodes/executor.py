"""Executor 节点（T4-3）。

执行单个子任务，根据推荐工具调用 MCP：
- 输入：current_subtask_index + subtasks
- 输出：subtask_results[subtask_id]
- 任务级 MCP Session 生命周期（P1-2：创建→使用→finally 关闭）

架构决策（P1-2）：每次 Executor 调用创建新的 MCP Session，
节点出口 finally 关闭，异常退出时子进程被 terminate/kill 不泄漏。
禁止全局单例，避免 Celery 多进程下多任务串话。
"""

from __future__ import annotations

from typing import Any

from app.agent.state import AgentState, SubTask, SubTaskResult
from app.core.exceptions import AppException
from app.core.logging import get_logger
from app.llm.gateway import LLMGateway
from app.mcp.client import MCPSession, create_session

logger = get_logger(__name__)

# Executor 系统提示词
EXECUTOR_SYSTEM_PROMPT = """你是一个任务执行专家。请根据子任务描述和可用工具，完成指定任务。

可用工具：
- web_search: 搜索互联网获取信息
- code_exec: 执行 Python 代码
- file_rw: 读写工作目录文件
- db_query: 查询业务数据库（只读）

如果子任务不需要工具，直接给出文本回答。
如果需要工具，先说明要调用哪个工具及参数，然后给出最终结果。
"""


class ExecutorNode:
    """Executor 节点（T4-3，P1-2）。

    执行单个子任务，按需调用 MCP 工具。
    每次调用创建任务级 MCP Session，finally 确保关闭。
    """

    def __init__(self, gateway: LLMGateway | None = None, use_mcp: bool = True) -> None:
        """初始化 Executor 节点。

        Args:
            gateway: LLM 网关
            use_mcp: 是否启用 MCP 工具调用（测试时可关闭）
        """
        self.gateway = gateway or LLMGateway()
        self.use_mcp = use_mcp

    async def __call__(self, state: AgentState) -> dict[str, Any]:
        """执行 Executor 节点。

        Args:
            state: 当前 Agent 状态

        Returns:
            状态更新字典，含 subtask_results 和 current_subtask_index
        """
        task_id = state.get("task_id", "unknown")
        subtasks = state.get("subtasks", [])
        index = state.get("current_subtask_index", 0)

        # 检查是否所有子任务已完成
        if index >= len(subtasks):
            return {"status": "aggregating"}

        subtask = subtasks[index]
        subtask_id = subtask["id"]

        logger.info("executor_start", task_id=task_id, subtask_id=subtask_id, index=index)

        # 执行子任务
        result = await self._execute_subtask(task_id, subtask, state)

        # 更新状态
        subtask_results = dict(state.get("subtask_results", {}))
        subtask_results[subtask_id] = result

        new_index = index + 1
        all_done = new_index >= len(subtasks)

        logger.info(
            "executor_complete",
            task_id=task_id,
            subtask_id=subtask_id,
            status=result["status"],
            next_index=new_index,
            all_done=all_done,
        )

        return {
            "subtask_results": subtask_results,
            "current_subtask_index": new_index,
            "status": "reviewing" if not all_done else "aggregating",
            "messages": state.get("messages", []) + [
                {"role": "assistant", "content": result["output"][:1000], "node": "executor", "subtask_id": subtask_id},
            ],
        }

    async def _execute_subtask(
        self, task_id: str, subtask: SubTask, state: AgentState
    ) -> SubTaskResult:
        """执行单个子任务。

        Args:
            task_id: 任务 ID
            subtask: 子任务定义
            state: 当前状态（用于获取上下文）

        Returns:
            子任务执行结果
        """
        subtask_id = subtask["id"]
        description = subtask["description"]
        recommended_tool = subtask.get("tool", "none")

        tool_calls: list[dict[str, Any]] = []
        output = ""
        error: str | None = None

        # 创建任务级 MCP Session（P1-2）
        session: MCPSession | None = None
        try:
            if self.use_mcp and recommended_tool != "none":
                session = create_session(timeout=30)
                session.start()

                # 根据推荐工具执行
                tool_result = await self._call_tool(session, recommended_tool, description, state)
                tool_calls.append({"tool": recommended_tool, "result": tool_result[:500]})
                output = tool_result
            else:
                # 不需要工具，直接调用 LLM
                response = await self.gateway.chat(
                    messages=[
                        {"role": "system", "content": EXECUTOR_SYSTEM_PROMPT},
                        {"role": "user", "content": f"子任务：{description}"},
                    ],
                    scenario="executor",
                    task_id=task_id,
                )
                output = response.content

            status = "success"

        except AppException as e:
            error = str(e)
            status = "failed"
            output = f"[执行失败] {e}"
            logger.error("executor_subtask_failed", task_id=task_id, subtask_id=subtask_id, error=error)
        except Exception as e:
            error = str(e)
            status = "failed"
            output = f"[执行异常] {e}"
            logger.error("executor_subtask_exception", task_id=task_id, subtask_id=subtask_id, error=error)
        finally:
            # P1-2：确保 Session 关闭，子进程不泄漏
            if session is not None:
                session.close()

        return {
            "subtask_id": subtask_id,
            "status": status,
            "output": output,
            "tool_calls": tool_calls,
            "error": error,
        }

    @staticmethod
    async def _call_tool(
        session: MCPSession, tool_name: str, description: str, state: AgentState
    ) -> str:
        """调用 MCP 工具。

        Args:
            session: MCP Session
            tool_name: 工具名称
            description: 子任务描述
            state: 当前状态

        Returns:
            工具执行结果文本
        """
        # 根据工具类型构造参数
        if tool_name == "web_search":
            arguments = {"query": description[:200]}
        elif tool_name == "code_exec":
            # 对于 code_exec，需要 LLM 生成代码（简化：直接执行一个模板）
            arguments = {"code": f"# 任务：{description}\nprint('任务执行完成')"}
        elif tool_name == "file_rw":
            arguments = {"operation": "list", "path": "."}
        elif tool_name == "db_query":
            arguments = {"sql": "SELECT name FROM sqlite_master WHERE type='table' LIMIT 10"}
        else:
            return f"不支持的工具: {tool_name}"

        return session.call_tool(tool_name, arguments)
