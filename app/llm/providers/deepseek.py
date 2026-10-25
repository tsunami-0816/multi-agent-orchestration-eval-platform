"""DeepSeek Provider（T2-2）。

基于 langchain-openai 实现 DeepSeek API 调用（OpenAI 兼容接口）。
支持自定义 base_url/api_key/model，API Key 从环境变量读取，禁止硬编码。

DeepSeek API 文档：https://api.deepseek.com/v1（OpenAI 兼容）
默认模型：deepseek-chat
"""

from __future__ import annotations

from typing import Any

from langchain_openai import ChatOpenAI

from app.core.exceptions import AppException, ErrorCode
from app.core.logging import get_logger
from app.llm.providers import BaseLLMProvider, LLMResponse, TokenUsage

logger = get_logger(__name__)


class DeepSeekProvider(BaseLLMProvider):
    """DeepSeek LLM Provider。

    通过 OpenAI 兼容接口调用 DeepSeek API。
    使用 langchain-openai 的 ChatOpenAI 类，配置 base_url 指向 DeepSeek。
    """

    name = "deepseek"

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com/v1",
        model: str = "deepseek-chat",
        temperature: float = 0.0,
        max_tokens: int = 4096,
        timeout: int = 60,
    ) -> None:
        """初始化 DeepSeek Provider。

        Args:
            api_key: DeepSeek API 密钥（从环境变量 APP_LLM_API_KEY 读取）
            base_url: API 基础 URL
            model: 模型名称
            temperature: 采样温度
            max_tokens: 最大生成 Token 数
            timeout: 请求超时（秒）
        """
        super().__init__(model=model, temperature=temperature, max_tokens=max_tokens, timeout=timeout)
        self.api_key = api_key
        self.base_url = base_url

        # 初始化 langchain-openai 客户端
        self._client = ChatOpenAI(
            model=model,
            api_key=api_key,
            base_url=base_url,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )

    async def achat(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> LLMResponse:
        """异步调用 DeepSeek 聊天补全。

        Args:
            messages: 消息列表 [{"role": "...", "content": "..."}]
            **kwargs: 额外参数（temperature/max_tokens 覆盖等）

        Returns:
            统一 LLMResponse 对象

        Raises:
            AppException(LLM_API_ERROR): API 调用失败
            AppException(LLM_TIMEOUT): 请求超时
        """
        try:
            # 转换为 langchain 消息格式
            from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

            lc_messages: list[BaseMessage] = []
            for msg in messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role == "system":
                    lc_messages.append(SystemMessage(content=content))
                elif role == "assistant":
                    lc_messages.append(AIMessage(content=content))
                else:
                    lc_messages.append(HumanMessage(content=content))

            # 应用覆盖参数
            temperature = kwargs.get("temperature", self.temperature)
            max_tokens = kwargs.get("max_tokens", self.max_tokens)

            # 异步调用
            response = await self._client.ainvoke(
                lc_messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            # 解析响应
            content = response.content if isinstance(response.content, str) else str(response.content)

            # 解析 usage
            usage = TokenUsage()
            if hasattr(response, "usage_metadata") and response.usage_metadata:
                usage.input_tokens = response.usage_metadata.get("input_tokens", 0)
                usage.output_tokens = response.usage_metadata.get("output_tokens", 0)
                usage.total_tokens = usage.input_tokens + usage.output_tokens

            logger.debug(
                "deepseek_call_success",
                model=self.model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
            )

            return LLMResponse(
                content=content,
                usage=usage,
                model=self.model,
                raw={"response": str(response)},
            )

        except Exception as e:
            # 区分超时和其他错误
            error_msg = str(e).lower()
            if "timeout" in error_msg or "timed out" in error_msg:
                logger.error("deepseek_call_timeout", model=self.model, error=str(e))
                raise AppException(ErrorCode.LLM_TIMEOUT, f"DeepSeek API 超时: {e}") from e
            logger.error("deepseek_call_failed", model=self.model, error=str(e))
            raise AppException(ErrorCode.LLM_API_ERROR, f"DeepSeek API 调用失败: {e}") from e
