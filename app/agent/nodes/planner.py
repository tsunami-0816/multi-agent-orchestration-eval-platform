"""Planner 节点（T4-2）。

调用 LLM 将用户输入分解为有序子任务列表：
- 输入：user_input
- 输出：subtasks（含 id/description/tool/depends_on）
- 每个子任务标注推荐 MCP 工具

架构决策（T-01）：Planner 输出严格 JSON，
包含子任务 ID、描述、推荐工具、依赖关系，
便于 Executor 按序执行和并行调度。
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.agent.state import AgentState, SubTask
from app.core.logging import get_logger
from app.llm.gateway import LLMGateway

logger = get_logger(__name__)

# Planner 系统提示词
PLANNER_SYSTEM_PROMPT = """你是一个任务规划专家。请将用户的复杂任务分解为有序的子任务列表。

要求：
1. 每个子任务应有明确的目标和可执行的描述
2. 为每个子任务推荐最合适的工具：
   - web_search: 需要搜索互联网获取最新信息时
   - code_exec: 需要执行代码进行计算/数据处理时
   - file_rw: 需要读写文件时
   - db_query: 需要查询业务数据库时
   - none: 不需要工具，纯文本推理时
3. 标注子任务间的依赖关系（depends_on）
4. 子任务数量控制在 2-5 个之间

输出格式（严格 JSON，不要包含其他文字）：
{
  "subtasks": [
    {
      "id": "subtask-001",
      "description": "子任务描述",
      "tool": "web_search",
      "depends_on": []
    }
  ]
}"""


class PlannerNode:
    """Planner 节点（T4-2）。

    调用 LLM 分解任务，输出子任务列表。
    """

    def __init__(self, gateway: LLMGateway | None = None) -> None:
        """初始化 Planner 节点。

        Args:
            gateway: LLM 网关，为 None 时使用全局单例
        """
        self.gateway = gateway or LLMGateway()

    async def __call__(self, state: AgentState) -> dict[str, Any]:
        """执行 Planner 节点。

        Args:
            state: 当前 Agent 状态

        Returns:
            状态更新字典，含 subtasks 和 messages
        """
        task_id = state.get("task_id", "unknown")
        user_input = state.get("user_input", "")

        logger.info("planner_start", task_id=task_id, input_length=len(user_input))

        # 调用 LLM
        response = await self.gateway.chat(
            messages=[
                {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
                {"role": "user", "content": user_input},
            ],
            scenario="planner",
            task_id=task_id,
        )

        # 解析 JSON 输出
        subtasks = self._parse_subtasks(response.content)

        logger.info("planner_complete", task_id=task_id, subtask_count=len(subtasks))

        return {
            "subtasks": subtasks,
            "current_subtask_index": 0,
            "status": "executing",
            "messages": state.get("messages", []) + [
                {"role": "assistant", "content": response.content, "node": "planner"},
            ],
        }

    @staticmethod
    def _parse_subtasks(content: str) -> list[SubTask]:
        """从 LLM 输出中解析子任务列表。

        Args:
            content: LLM 返回的文本（期望为 JSON）

        Returns:
            子任务列表，解析失败时返回默认单任务
        """
        # 尝试提取 JSON（可能被 markdown 代码块包裹）
        json_match = re.search(r"\{[\s\S]*\}", content)
        if json_match:
            try:
                data = json.loads(json_match.group())
                raw_subtasks = data.get("subtasks", [])
                subtasks: list[SubTask] = []
                for i, st in enumerate(raw_subtasks):
                    subtasks.append({
                        "id": st.get("id", f"subtask-{i+1:03d}"),
                        "description": st.get("description", ""),
                        "tool": st.get("tool", "none"),
                        "depends_on": st.get("depends_on", []),
                    })
                if subtasks:
                    return subtasks
            except (json.JSONDecodeError, KeyError):
                pass

        # 解析失败时返回默认单任务
        logger.warning("planner_parse_failed, using default single subtask")
        return [{
            "id": "subtask-001",
            "description": content[:500] if content else "执行用户请求",
            "tool": "none",
            "depends_on": [],
        }]
