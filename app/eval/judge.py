"""Judge 评分器（T6-3）。

调用 LLM 作为评审员，对模型输出进行多维度评分：
- 准确性（accuracy）：输出是否正确、无事实错误
- 相关性（relevance）：输出是否与问题相关
- 完整性（completeness）：是否完整回答了问题
- 格式（format）：输出格式是否清晰、可用

架构决策（T-01）：使用 LLM-as-Judge 进行评分，
输出严格 JSON，包含 4 维度分数和反馈文本。
Mock 模式下返回预设评分（planner 场景识别为 judge）。
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from app.core.logging import get_logger
from app.llm.gateway import LLMGateway

logger = get_logger(__name__)

# Judge 系统提示词
JUDGE_SYSTEM_PROMPT = """你是一个严格的评审专家。请根据评分标准，对模型输出进行多维度评分。

评分维度（每项 0-10 分）：
1. accuracy（准确性）：输出是否正确、无事实错误、无幻觉
2. relevance（相关性）：输出是否与问题直接相关，不跑题
3. completeness（完整性）：是否完整回答了问题的所有方面
4. format（格式）：输出格式是否清晰、结构是否合理、易于阅读

输出格式（严格 JSON，不要包含其他文字）：
{
  "accuracy": 0-10,
  "relevance": 0-10,
  "completeness": 0-10,
  "format": 0-10,
  "feedback": "简要评价，指出优点和不足"
}"""


class EvalJudge:
    """LLM 评审员（T6-3）。

    调用 LLM 对模型输出进行多维度评分。
    """

    def __init__(self, gateway: Optional[LLMGateway] = None) -> None:
        """初始化 Judge。

        Args:
            gateway: LLM 网关，为 None 时使用全局单例
        """
        self.gateway = gateway or LLMGateway()

    async def evaluate(
        self,
        case_input: str,
        model_output: str,
        scoring_criteria: str,
        run_id: str = "",
    ) -> dict[str, Any]:
        """执行评分。

        Args:
            case_input: 用例输入（问题）
            model_output: 模型输出（待评分的回答）
            scoring_criteria: 评分标准
            run_id: 评测运行 ID（用于日志追踪）

        Returns:
            评分结果字典，含 accuracy/relevance/completeness/format/feedback
        """
        # 构建用户消息
        user_message = (
            f"【问题】\n{case_input}\n\n"
            f"【评分标准】\n{scoring_criteria}\n\n"
            f"【模型输出】\n{model_output[:3000]}\n\n"
            f"请根据评分标准，对模型输出进行评分。"
        )

        # 调用 LLM（scenario=judge，Mock 模式返回预设评分）
        response = await self.gateway.chat(
            messages=[
                {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            scenario="judge",
            task_id=run_id,
        )

        # 解析评分结果
        result = self._parse_judge_response(response.content)
        logger.info(
            "eval_judge_complete",
            run_id=run_id,
            accuracy=result.get("accuracy"),
            overall=sum(result.get(k, 0) for k in ("accuracy", "relevance", "completeness", "format")) / 4,
        )
        return result

    @staticmethod
    def _parse_judge_response(content: str) -> dict[str, Any]:
        """解析 Judge 的 JSON 输出。

        Args:
            content: LLM 返回的文本

        Returns:
            评分结果字典，解析失败时返回默认评分
        """
        # 尝试提取 JSON
        json_match = re.search(r"\{[\s\S]*\}", content)
        if json_match:
            try:
                data = json.loads(json_match.group())
                return {
                    "accuracy": float(data.get("accuracy", 7.0)),
                    "relevance": float(data.get("relevance", 7.0)),
                    "completeness": float(data.get("completeness", 7.0)),
                    "format": float(data.get("format", 7.0)),
                    "feedback": str(data.get("feedback", "")),
                }
            except (json.JSONDecodeError, ValueError, TypeError):
                pass

        # 解析失败时返回默认评分
        logger.warning("judge_parse_failed, using default scores")
        return {
            "accuracy": 7.0,
            "relevance": 7.0,
            "completeness": 7.0,
            "format": 7.0,
            "feedback": "默认评分（解析失败）",
        }
