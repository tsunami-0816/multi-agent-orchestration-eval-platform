"""Aggregator 节点（T4-5）。

汇总全部子任务结果，生成最终输出：
- 输入：全部 subtask_results
- 输出：final_output（结构化文本）

架构决策（T-01）：Aggregator 调用 LLM 进行语义汇总，
而非简单拼接，确保最终输出连贯、有逻辑。
"""

from __future__ import annotations

from typing import Any

from app.agent.state import AgentState
from app.core.logging import get_logger
from app.llm.gateway import LLMGateway

logger = get_logger(__name__)

# Aggregator 系统提示词
AGGREGATOR_SYSTEM_PROMPT = """你是一个结果汇总专家。请将多个子任务的执行结果汇总为一个连贯、完整的最终回答。

要求：
1. 综合所有子任务的结果，不要遗漏重要信息
2. 按逻辑顺序组织内容，确保连贯性
3. 直接回答用户的原始问题
4. 如果某些子任务失败，在最终回答中说明
5. 输出格式清晰，适当使用标题和列表
"""


class AggregatorNode:
    """Aggregator 节点（T4-5）。

    汇总全部子任务结果，生成最终输出。
    """

    def __init__(self, gateway: LLMGateway | None = None) -> None:
        """初始化 Aggregator 节点。

        Args:
            gateway: LLM 网关
        """
        self.gateway = gateway or LLMGateway()

    async def __call__(self, state: AgentState) -> dict[str, Any]:
        """执行 Aggregator 节点。

        Args:
            state: 当前 Agent 状态

        Returns:
            状态更新字典，含 final_output 和 status
        """
        task_id = state.get("task_id", "unknown")
        user_input = state.get("user_input", "")
        subtasks = state.get("subtasks", [])
        subtask_results = state.get("subtask_results", {})

        logger.info("aggregator_start", task_id=task_id, subtask_count=len(subtasks))

        # 构建汇总上下文
        context_parts = [f"用户原始请求：{user_input}", "", "子任务执行结果："]
        for i, subtask in enumerate(subtasks, 1):
            result = subtask_results.get(subtask["id"], {})
            status = result.get("status", "unknown")
            output = result.get("output", "(无输出)")[:1000]
            context_parts.append(f"\n{i}. {subtask['description']} [{status}]")
            context_parts.append(f"   结果：{output}")

        context = "\n".join(context_parts)

        # 调用 LLM 汇总
        response = await self.gateway.chat(
            messages=[
                {"role": "system", "content": AGGREGATOR_SYSTEM_PROMPT},
                {"role": "user", "content": context},
            ],
            scenario="default",
            task_id=task_id,
        )

        final_output = response.content

        logger.info("aggregator_complete", task_id=task_id, output_length=len(final_output))

        return {
            "final_output": final_output,
            "status": "completed",
            "messages": state.get("messages", []) + [
                {"role": "assistant", "content": final_output, "node": "aggregator"},
            ],
        }
