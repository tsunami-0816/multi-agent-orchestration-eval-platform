"""Reviewer 节点（T4-4）。

审查子任务执行结果，决定通过/打回重做：
- 输入：subtask_results[current]
- 输出：review_results[subtask_id]（passed/feedback/score）
- 未通过的子任务标记需重做（回到 Executor）

架构决策（T-01）：Reviewer 输出严格 JSON，
包含 passed 布尔值、feedback 文本、score 数值，
便于条件路由判断是否需要重做。
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.agent.state import AgentState, ReviewResult
from app.core.logging import get_logger
from app.llm.gateway import LLMGateway

logger = get_logger(__name__)

# Reviewer 系统提示词
REVIEWER_SYSTEM_PROMPT = """你是一个质量审查专家。请审查子任务的执行结果是否满足要求。

审查维度：
1. 准确性：结果是否正确、无事实错误
2. 完整性：是否完整回答了子任务要求
3. 相关性：结果是否与子任务目标相关
4. 格式：输出格式是否清晰、可用

输出格式（严格 JSON，不要包含其他文字）：
{
  "passed": true/false,
  "feedback": "审查反馈，如未通过请说明修改建议",
  "score": 0-10的数值评分
}"""


class ReviewerNode:
    """Reviewer 节点（T4-4）。

    审查子任务执行结果，决定通过或打回重做。
    """

    def __init__(self, gateway: LLMGateway | None = None) -> None:
        """初始化 Reviewer 节点。

        Args:
            gateway: LLM 网关
        """
        self.gateway = gateway or LLMGateway()

    async def __call__(self, state: AgentState) -> dict[str, Any]:
        """执行 Reviewer 节点。

        Args:
            state: 当前 Agent 状态

        Returns:
            状态更新字典，含 review_results
        """
        task_id = state.get("task_id", "unknown")
        subtasks = state.get("subtasks", [])
        index = state.get("current_subtask_index", 0)
        subtask_results = state.get("subtask_results", {})

        # 获取当前子任务结果
        if index > 0 and index <= len(subtasks):
            current_subtask = subtasks[index - 1]
            subtask_id = current_subtask["id"]
            result = subtask_results.get(subtask_id)

            if result and result["status"] == "success":
                review = await self._review_result(task_id, current_subtask, result)

                review_results = dict(state.get("review_results", {}))
                review_results[subtask_id] = review

                logger.info(
                    "reviewer_complete",
                    task_id=task_id,
                    subtask_id=subtask_id,
                    passed=review["passed"],
                    score=review["score"],
                )

                # 如果未通过且未超过最大轮次，标记重做
                if not review["passed"] and state.get("round_count", 0) < state.get("max_rounds", 5):
                    return {
                        "review_results": review_results,
                        "current_subtask_index": index - 1,  # 回退重做
                        "round_count": state.get("round_count", 0) + 1,
                        "status": "executing",
                        "messages": state.get("messages", []) + [
                            {"role": "assistant", "content": f"审查未通过: {review['feedback']}", "node": "reviewer"},
                        ],
                    }

                return {
                    "review_results": review_results,
                    "status": "executing" if index < len(subtasks) else "aggregating",
                    "messages": state.get("messages", []) + [
                        {"role": "assistant", "content": f"审查通过（{review['score']}/10）", "node": "reviewer"},
                    ],
                }

        # 没有需要审查的结果，直接进入下一阶段
        return {"status": "executing" if index < len(subtasks) else "aggregating"}

    async def _review_result(
        self, task_id: str, subtask: dict[str, Any], result: dict[str, Any]
    ) -> ReviewResult:
        """审查单个子任务结果。

        Args:
            task_id: 任务 ID
            subtask: 子任务定义
            result: 子任务执行结果

        Returns:
            审查结果
        """
        response = await self.gateway.chat(
            messages=[
                {"role": "system", "content": REVIEWER_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"子任务：{subtask['description']}\n\n"
                        f"执行结果：{result['output'][:2000]}"
                    ),
                },
            ],
            scenario="reviewer",
            task_id=task_id,
        )

        return self._parse_review(response.content)

    @staticmethod
    def _parse_review(content: str) -> ReviewResult:
        """解析 Reviewer 输出。

        Args:
            content: LLM 返回的文本

        Returns:
            审查结果，解析失败时默认通过
        """
        json_match = re.search(r"\{[\s\S]*\}", content)
        if json_match:
            try:
                data = json.loads(json_match.group())
                return {
                    "passed": bool(data.get("passed", True)),
                    "feedback": str(data.get("feedback", "")),
                    "score": float(data.get("score", 7.0)),
                }
            except (json.JSONDecodeError, ValueError):
                pass

        # 解析失败时默认通过
        return {"passed": True, "feedback": "默认通过（解析失败）", "score": 7.0}
