"""统一 LLM 网关（T2-1/T2-4/T2-5/T2-6，P1-9）。

LLMGateway 是所有 LLM 调用的统一入口，提供：
1. Provider 工厂：配置驱动选择 deepseek/qwen/ollama/mock
2. 指数退避重试：最多 3 次，初始 1s，backoff_factor=2（T2-4）
3. Token 计数与费用计算：按配置单价计算费用（T2-5）
4. 用量持久化与预算控制：写入 llm_usage 表，超 ¥180 强制 Mock，超 ¥200 拒绝（T2-6/P1-9）

架构决策（P1-9）：LLM 费用预算控制通过 Gateway 层统一实现，
所有 LLM 调用必须经过 Gateway，确保计费和预算控制不被绕过。
"""

from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import Any, Optional

from app.config import AppConfig, get_config
from app.core.exceptions import AppException, ErrorCode, LLMBudgetExceededException
from app.core.logging import get_logger
from app.llm.mock import MockProvider
from app.llm.providers import BaseLLMProvider, LLMResponse, TokenUsage

logger = get_logger(__name__)


class LLMGateway:
    """统一 LLM 网关。

    所有 LLM 调用的唯一入口，封装 Provider 选择、重试、计费和预算控制。
    使用单例模式，全局共享一个实例。
    """

    def __init__(self, config: Optional[AppConfig] = None) -> None:
        """初始化网关。

        Args:
            config: 应用配置，为 None 时使用全局配置
        """
        self.config = config or get_config()
        self._provider: Optional[BaseLLMProvider] = None
        self._force_mock: bool = False
        self._daily_cost_cache: float = 0.0
        self._cache_dirty: bool = True

    def _create_provider(self) -> BaseLLMProvider:
        """根据配置创建 LLM Provider。

        支持 deepseek / qwen / ollama / mock 四种 Provider。
        qwen 和 ollama 为 P2 备选（T2-7），未实现时回退到 mock。

        Returns:
            具体 Provider 实例
        """
        provider_name = self.config.llm_provider.lower()

        if provider_name == "mock":
            return MockProvider(model="mock-model")

        # DeepSeek / Qwen(通义千问 DashScope) 都是 OpenAI 兼容协议，复用同一 Provider 实现。
        # 通过 APP_LLM_BASE_URL 切换端点：DeepSeek 用 https://api.deepseek.com/v1，
        # Qwen 用 https://dashscope.aliyuncs.com/compatible-mode/v1（或百炼工作空间端点）。
        if provider_name in ("deepseek", "qwen"):
            from app.llm.providers.deepseek import DeepSeekProvider

            return DeepSeekProvider(
                api_key=self.config.llm_api_key,
                base_url=self.config.llm_base_url,
                model=self.config.llm_model,
                temperature=self.config.llm_temperature,
                max_tokens=self.config.llm_max_tokens,
                timeout=self.config.llm_timeout,
            )

        # Ollama 本地模型暂未实现，回退 mock
        if provider_name == "ollama":
            logger.warning("provider_not_implemented_fallback_mock", provider=provider_name)
            return MockProvider(model="ollama-mock-fallback")

        logger.warning("unknown_provider_fallback_mock", provider=provider_name)
        return MockProvider(model="unknown-mock-fallback")

    @property
    def provider(self) -> BaseLLMProvider:
        """获取当前 Provider（懒加载）。

        强制 Mock 模式下返回 MockProvider，忽略配置的 Provider。
        """
        if self._force_mock:
            if not isinstance(self._provider, MockProvider):
                self._provider = MockProvider(model="forced-mock")
            return self._provider

        if self._provider is None:
            self._provider = self._create_provider()
        return self._provider

    def _calculate_cost(self, model: str, usage: TokenUsage) -> float:
        """计算本次调用费用（T2-5）。

        按配置单价计算：input_tokens × 单价_in + output_tokens × 单价_out
        Mock/本地模型计费为 0。
        费用单位元，保留 4 位小数。

        Args:
            model: 模型标识
            usage: Token 用量

        Returns:
            费用（元）
        """
        # Mock 模式计费为 0
        if model.startswith("mock") or "mock" in model:
            return 0.0

        # 从配置获取单价（简化版，实际从 config.yaml 的 llm.pricing 读取）
        # 默认单价：input 0.001 元/千token，output 0.002 元/千token
        input_price = 0.001  # 元/千 token
        output_price = 0.002  # 元/千 token

        cost = (usage.input_tokens / 1000) * input_price + (usage.output_tokens / 1000) * output_price
        return round(cost, 4)

    async def _get_daily_cost(self) -> float:
        """获取当日累计 LLM 费用（P1-9 预算控制用）。

        带简单缓存，避免每次调用都查库。
        写入新记录后标记缓存为 dirty。

        Returns:
            当日累计费用（元）
        """
        if self._cache_dirty:
            try:
                from app.models.base import get_session_factory
                from app.repositories.usage_repo import UsageRepository

                factory = get_session_factory(self.config.database_url)
                async with factory() as session:
                    repo = UsageRepository(session)
                    self._daily_cost_cache = await repo.get_daily_cost()
                self._cache_dirty = False
            except Exception as e:
                logger.warning("daily_cost_query_failed", error=str(e))
                # 查询失败时使用缓存值，不阻断调用
        return self._daily_cost_cache

    async def _record_usage(
        self,
        model: str,
        usage: TokenUsage,
        cost: float,
        task_id: Optional[str] = None,
    ) -> None:
        """持久化 LLM 用量记录（T2-6）。

        写入 llm_usage 表，并更新当日费用缓存。

        Args:
            model: 模型标识
            usage: Token 用量
            cost: 本次费用
            task_id: 关联任务 ID
        """
        try:
            from app.models.base import get_session_factory
            from app.repositories.usage_repo import UsageRepository

            factory = get_session_factory(self.config.database_url)
            async with factory() as session:
                repo = UsageRepository(session)
                await repo.insert_usage(
                    model=model,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cost_yuan=cost,
                    task_id=task_id,
                )
                await session.commit()
            self._cache_dirty = True
            self._daily_cost_cache += cost
        except Exception as e:
            logger.error("usage_record_failed", error=str(e))
            # 用量记录失败不阻断主流程，但记录错误

    async def _check_budget(self) -> None:
        """检查 LLM 预算（P1-9）。

        - 超 ¥180：告警 + 新任务强制切换 Mock（在途任务继续）
        - 超 ¥200：拒绝新任务，返回 50003

        Raises:
            LLMBudgetExceededException: 超过 ¥200 上限时抛出
        """
        if self._force_mock:
            return  # 已在 Mock 模式，不检查

        daily_cost = await self._get_daily_cost()

        if daily_cost >= self.config.budget_daily_limit:
            logger.error(
                "llm_budget_exceeded",
                daily_cost=daily_cost,
                limit=self.config.budget_daily_limit,
            )
            raise LLMBudgetExceededException(daily_cost, self.config.budget_daily_limit)

        if daily_cost >= self.config.budget_warning_threshold:
            logger.warning(
                "llm_budget_warning_force_mock",
                daily_cost=daily_cost,
                threshold=self.config.budget_warning_threshold,
            )
            # P1-9：达到预警阈值后新任务强制切换 Mock
            self._force_mock = True

    async def chat(
        self,
        messages: list[dict[str, str]],
        task_id: Optional[str] = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """统一 LLM 聊天接口（带重试、计费、预算控制）。

        执行流程：
        1. 预算检查（非 Mock 模式）
        2. 指数退避重试调用 Provider
        3. 计算费用
        4. 持久化用量记录
        5. 返回统一响应

        Args:
            messages: 消息列表
            task_id: 关联任务 ID（用于用量追踪）
            **kwargs: 额外参数（传递给 Provider）

        Returns:
            统一 LLMResponse 对象

        Raises:
            LLMBudgetExceededException: 预算超限
            AppException(LLM_API_ERROR): 重试耗尽后 API 仍失败
            AppException(LLM_TIMEOUT): 重试耗尽后仍超时
        """
        # 1. 预算检查（仅非强制 Mock 模式）
        if not self._force_mock and not self.provider.is_mock():
            await self._check_budget()

        # 2. 指数退避重试（T2-4）
        max_attempts = self.config.llm_retry_max_attempts
        initial_delay = self.config.llm_retry_initial_delay
        backoff_factor = self.config.llm_retry_backoff_factor

        last_exception: Optional[Exception] = None

        for attempt in range(1, max_attempts + 1):
            try:
                response = await self.provider.achat(messages, **kwargs)

                # 3. 计算费用（T2-5）
                cost = self._calculate_cost(response.model or self.provider.model, response.usage)

                # 4. 持久化用量（T2-6）
                if cost > 0 or not self.provider.is_mock():
                    await self._record_usage(
                        model=response.model or self.provider.model,
                        usage=response.usage,
                        cost=cost,
                        task_id=task_id,
                    )

                logger.info(
                    "llm_call_success",
                    provider=self.provider.name,
                    model=response.model or self.provider.model,
                    attempt=attempt,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    cost_yuan=cost,
                    task_id=task_id,
                )

                return response

            except AppException as e:
                last_exception = e
                # 预算超限不重试
                if e.code == ErrorCode.LLM_BUDGET_EXCEEDED:
                    raise
                # 超时或 API 错误可重试
                if e.code in (ErrorCode.LLM_TIMEOUT, ErrorCode.LLM_API_ERROR):
                    if attempt < max_attempts:
                        delay = initial_delay * (backoff_factor ** (attempt - 1))
                        logger.warning(
                            "llm_call_retry",
                            attempt=attempt,
                            max_attempts=max_attempts,
                            delay=delay,
                            error=str(e),
                        )
                        await asyncio.sleep(delay)
                        continue
                raise

            except Exception as e:
                last_exception = e
                if attempt < max_attempts:
                    delay = initial_delay * (backoff_factor ** (attempt - 1))
                    logger.warning(
                        "llm_call_retry_unexpected",
                        attempt=attempt,
                        delay=delay,
                        error=str(e),
                    )
                    await asyncio.sleep(delay)
                    continue
                raise AppException(ErrorCode.LLM_API_ERROR, f"LLM 调用失败: {e}") from e

        # 重试耗尽
        if last_exception:
            raise last_exception
        raise AppException(ErrorCode.LLM_API_ERROR, "LLM 调用失败：重试耗尽")


# === 全局单例 ===

_gateway_instance: Optional[LLMGateway] = None


def get_gateway() -> LLMGateway:
    """获取全局 LLMGateway 单例。

    Returns:
        LLMGateway 实例
    """
    global _gateway_instance
    if _gateway_instance is None:
        _gateway_instance = LLMGateway()
    return _gateway_instance


def reset_gateway() -> None:
    """重置网关单例（测试用）。"""
    global _gateway_instance
    _gateway_instance = None
