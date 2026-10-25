"""Mock LLM Provider（T2-3）。

实现 Mock 模式：根据输入场景返回预设响应模板，零成本调试用。
APP_LLM_PROVIDER=mock 时全部 LLM 调用零成本返回。

支持的场景（通过 system prompt 或第一条消息中的角色标识识别）：
- planner：返回固定子任务 JSON
- executor：返回固定执行结果
- reviewer/judge：返回固定分数 JSON
- default：返回通用文本响应

响应格式与真实 API 一致，可被下游（Agent/Judge）正常解析。
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.core.logging import get_logger
from app.llm.providers import BaseLLMProvider, LLMResponse, TokenUsage

logger = get_logger(__name__)

# === 预设响应模板 ===

# Planner 场景：返回子任务拆解 JSON
_PLANNER_RESPONSE = json.dumps(
    {
        "subtasks": [
            {
                "id": "subtask-1",
                "title": "信息收集",
                "description": "收集与任务相关的背景信息和参考资料",
                "depends_on": [],
            },
            {
                "id": "subtask-2",
                "title": "分析处理",
                "description": "对收集到的信息进行分析和处理",
                "depends_on": ["subtask-1"],
            },
            {
                "id": "subtask-3",
                "title": "结果汇总",
                "description": "整合分析结果，生成最终输出",
                "depends_on": ["subtask-2"],
            },
        ]
    },
    ensure_ascii=False,
    indent=2,
)

# Executor 场景：返回执行结果
_EXECUTOR_RESPONSE = (
    "任务执行完成。\n\n"
    "执行步骤：\n"
    "1. 分析任务需求，确定执行方案\n"
    "2. 调用相关工具获取必要信息\n"
    "3. 整合信息并生成结果\n\n"
    "执行结果：\n"
    "基于输入信息，已完成指定任务的处理。"
    "结果包含完整的分析过程和最终结论，可直接用于后续环节。"
)

# Reviewer 场景：返回审查结果 JSON
_REVIEWER_RESPONSE = json.dumps(
    {
        "passed": True,
        "feedback": "输出质量良好，内容完整且符合要求。结构清晰，逻辑连贯，无明显错误。",
    },
    ensure_ascii=False,
)

# Judge 场景：返回评分 JSON（4 维度）
_JUDGE_RESPONSE = json.dumps(
    {
        "accuracy_score": 8.5,
        "relevance_score": 8.0,
        "completeness_score": 7.5,
        "format_score": 9.0,
        "judge_feedback": "输出整体质量良好，准确性较高，与问题相关度强。"
        "内容较为完整，格式规范。建议在细节深度上进一步提升。",
    },
    ensure_ascii=False,
    indent=2,
)

# 默认响应
_DEFAULT_RESPONSE = (
    "这是 Mock 模式的默认响应。\n"
    "当前配置为 APP_LLM_PROVIDER=mock，所有 LLM 调用零成本返回预设内容。\n"
    "如需真实 API 响应，请设置 APP_LLM_PROVIDER=deepseek 并配置 APP_LLM_API_KEY。"
)


def _detect_scenario(messages: list[dict[str, str]]) -> str:
    """从消息中识别场景类型。

    通过 system prompt 或消息内容中的关键词识别：
    - planner：任务拆解
    - executor：任务执行
    - reviewer：质量审查
    - judge：评分
    - default：通用

    Args:
        messages: 消息列表

    Returns:
        场景标识字符串
    """
    all_text = " ".join(msg.get("content", "") for msg in messages).lower()

    if "planner" in all_text or "拆解" in all_text or "subtask" in all_text:
        return "planner"
    if "judge" in all_text or "评分" in all_text or "accuracy_score" in all_text:
        return "judge"
    if "reviewer" in all_text or "审查" in all_text or "passed" in all_text:
        return "reviewer"
    if "executor" in all_text or "执行" in all_text or "tool" in all_text:
        return "executor"
    return "default"


class MockProvider(BaseLLMProvider):
    """Mock LLM Provider。

    零成本返回预设响应，用于开发调试和单元测试。
    计费为 0，不消耗 API 额度。
    """

    name = "mock"

    def __init__(self, model: str = "mock-model", **kwargs: Any) -> None:
        """初始化 Mock Provider。

        Args:
            model: 模型名称（仅用于标识，不影响响应）
            **kwargs: 忽略其他参数（保持接口兼容）
        """
        super().__init__(model=model, temperature=0.0, max_tokens=4096, timeout=60)

    async def achat(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> LLMResponse:
        """异步返回预设 Mock 响应。

        根据消息内容识别场景，返回对应预设模板。
        模拟少量延迟（可选），计费为 0。

        Args:
            messages: 消息列表
            **kwargs: 额外参数（忽略）

        Returns:
            统一 LLMResponse 对象（usage 全为 0）
        """
        scenario = _detect_scenario(messages)

        # 根据场景选择响应模板
        response_map = {
            "planner": _PLANNER_RESPONSE,
            "executor": _EXECUTOR_RESPONSE,
            "reviewer": _REVIEWER_RESPONSE,
            "judge": _JUDGE_RESPONSE,
        }
        content = response_map.get(scenario, _DEFAULT_RESPONSE)

        # Mock 模式 usage 全为 0（零成本）
        usage = TokenUsage(input_tokens=0, output_tokens=0, total_tokens=0)

        logger.debug(
            "mock_call",
            scenario=scenario,
            model=self.model,
            content_length=len(content),
        )

        return LLMResponse(
            content=content,
            usage=usage,
            model=self.model,
            raw={"scenario": scenario, "mock": True},
        )
