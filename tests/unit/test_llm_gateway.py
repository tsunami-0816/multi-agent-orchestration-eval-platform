"""LLM 网关单元测试（T2-1/T2-3/T2-4/T2-5）。

测试 Mock Provider、场景识别、费用计算、重试机制。
"""

from __future__ import annotations

import pytest

from app.core.exceptions import AppException, ErrorCode
from app.llm.gateway import LLMGateway
from app.llm.mock import MockProvider, _detect_scenario
from app.llm.providers import LLMResponse, TokenUsage


class TestMockProvider:
    """Mock Provider 测试。"""

    @pytest.mark.asyncio
    async def test_default_response(self, mock_llm_provider) -> None:
        """测试默认响应。"""
        messages = [{"role": "user", "content": "你好"}]
        response = await mock_llm_provider.achat(messages)
        assert isinstance(response, LLMResponse)
        assert len(response.content) > 0
        assert response.usage.input_tokens == 0
        assert response.usage.output_tokens == 0
        assert response.model == "test-mock"

    @pytest.mark.asyncio
    async def test_planner_scenario_returns_json(self, mock_llm_provider) -> None:
        """测试 Planner 场景返回 JSON 子任务。"""
        import json

        messages = [
            {"role": "system", "content": "你是一个 planner，负责任务拆解"},
            {"role": "user", "content": "请拆解这个任务"},
        ]
        response = await mock_llm_provider.achat(messages)
        data = json.loads(response.content)
        assert "subtasks" in data
        assert len(data["subtasks"]) >= 2
        assert "id" in data["subtasks"][0]
        assert "title" in data["subtasks"][0]

    @pytest.mark.asyncio
    async def test_judge_scenario_returns_scores(self, mock_llm_provider) -> None:
        """测试 Judge 场景返回 4 维度评分。"""
        import json

        messages = [
            {"role": "system", "content": "你是一个 judge，负责评分 accuracy_score relevance_score"},
            {"role": "user", "content": "请评分"},
        ]
        response = await mock_llm_provider.achat(messages)
        data = json.loads(response.content)
        assert "accuracy_score" in data
        assert "relevance_score" in data
        assert "completeness_score" in data
        assert "format_score" in data
        assert "judge_feedback" in data

    @pytest.mark.asyncio
    async def test_reviewer_scenario_returns_passed(self, mock_llm_provider) -> None:
        """测试 Reviewer 场景返回审查结果。"""
        import json

        messages = [{"role": "user", "content": "请审查这个输出，判断 passed 还是 fail"}]
        response = await mock_llm_provider.achat(messages)
        data = json.loads(response.content)
        assert "passed" in data
        assert "feedback" in data

    def test_is_mock(self, mock_llm_provider) -> None:
        """测试 is_mock 标识。"""
        assert mock_llm_provider.is_mock() is True


class TestScenarioDetection:
    """场景识别测试。"""

    def test_detect_planner(self) -> None:
        messages = [{"role": "system", "content": "你是 planner，负责拆解 subtask"}]
        assert _detect_scenario(messages) == "planner"

    def test_detect_judge(self) -> None:
        messages = [{"role": "system", "content": "你是 judge，请评分 accuracy_score"}]
        assert _detect_scenario(messages) == "judge"

    def test_detect_reviewer(self) -> None:
        messages = [{"role": "user", "content": "请审查，判断 passed"}]
        assert _detect_scenario(messages) == "reviewer"

    def test_detect_executor(self) -> None:
        messages = [{"role": "user", "content": "执行任务，调用 tool"}]
        assert _detect_scenario(messages) == "executor"

    def test_detect_default(self) -> None:
        messages = [{"role": "user", "content": "你好世界"}]
        assert _detect_scenario(messages) == "default"


class TestLLMResponse:
    """统一响应模型测试。"""

    def test_token_usage_total_auto_calc(self) -> None:
        """测试 total_tokens 自动计算。"""
        usage = TokenUsage(input_tokens=100, output_tokens=50)
        response = LLMResponse(content="test", usage=usage)
        assert response.usage.total_tokens == 150

    def test_response_with_zero_usage(self) -> None:
        """测试零用量响应。"""
        response = LLMResponse(content="test")
        assert response.usage.input_tokens == 0
        assert response.usage.total_tokens == 0


class TestLLMGateway:
    """LLM 网关测试。"""

    def test_gateway_uses_mock_provider(self, app_config) -> None:
        """测试网关在 Mock 配置下使用 MockProvider。"""
        gateway = LLMGateway(config=app_config)
        assert gateway.provider.is_mock() is True

    def test_calculate_cost_mock_is_zero(self, app_config) -> None:
        """测试 Mock 模式费用为 0。"""
        gateway = LLMGateway(config=app_config)
        usage = TokenUsage(input_tokens=10000, output_tokens=5000)
        cost = gateway._calculate_cost("mock-model", usage)
        assert cost == 0.0

    def test_calculate_cost_real_model(self, app_config) -> None:
        """测试真实模型费用计算。"""
        gateway = LLMGateway(config=app_config)
        usage = TokenUsage(input_tokens=1000, output_tokens=500)
        cost = gateway._calculate_cost("deepseek-chat", usage)
        # input: 1000/1000 * 0.001 = 0.001
        # output: 500/1000 * 0.002 = 0.001
        # total: 0.002
        assert cost == 0.002

    @pytest.mark.asyncio
    async def test_gateway_chat_mock_success(self, app_config) -> None:
        """测试网关 Mock 模式调用成功。"""
        gateway = LLMGateway(config=app_config)
        response = await gateway.chat([{"role": "user", "content": "测试"}])
        assert isinstance(response, LLMResponse)
        assert len(response.content) > 0

    @pytest.mark.asyncio
    async def test_gateway_force_mock_mode(self, app_config) -> None:
        """测试强制 Mock 模式。"""
        gateway = LLMGateway(config=app_config)
        gateway._force_mock = True
        assert gateway.provider.is_mock() is True

        response = await gateway.chat([{"role": "user", "content": "测试"}])
        assert isinstance(response, LLMResponse)
