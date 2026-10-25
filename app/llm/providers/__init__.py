"""LLM Provider 基类与响应模型（T2-1）。

定义统一的 LLM 调用接口，所有 Provider（DeepSeek/Qwen/Ollama/Mock）继承此基类。
配置驱动选择 Provider，切换时不修改调用代码。

核心设计：
- LLMResponse：统一响应模型（content + usage）
- BaseLLMProvider：抽象基类，定义 achat 异步接口
- 重试与超时在 Gateway 层统一处理（T2-4）
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class TokenUsage:
    """Token 用量统计。

    Attributes:
        input_tokens: 输入 Token 数
        output_tokens: 输出 Token 数
        total_tokens: 总 Token 数
    """

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass
class LLMResponse:
    """统一 LLM 响应模型。

    所有 Provider 返回此统一格式，下游（Agent/Judge）无需关心具体 Provider。

    Attributes:
        content: 响应文本内容
        usage: Token 用量统计
        model: 实际使用的模型标识
        raw: 原始响应（调试用，可选）
    """

    content: str
    usage: TokenUsage = field(default_factory=TokenUsage)
    model: str = ""
    raw: Optional[dict[str, Any]] = None

    def __post_init__(self) -> None:
        """初始化后校验 total_tokens。"""
        if self.usage.total_tokens == 0 and (self.usage.input_tokens or self.usage.output_tokens):
            self.usage.total_tokens = self.usage.input_tokens + self.usage.output_tokens


class BaseLLMProvider(ABC):
    """LLM Provider 抽象基类。

    所有具体 Provider 必须实现 achat 方法。
    Gateway 层通过此接口调用，实现 Provider 无关性。

    Attributes:
        name: Provider 名称（deepseek/qwen/ollama/mock）
        model: 默认模型名称
    """

    name: str = "base"

    def __init__(self, model: str = "", temperature: float = 0.0, max_tokens: int = 4096, timeout: int = 60) -> None:
        """初始化 Provider。

        Args:
            model: 模型名称
            temperature: 采样温度（0 最大化确定性）
            max_tokens: 最大生成 Token 数
            timeout: 请求超时（秒）
        """
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout

    @abstractmethod
    async def achat(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> LLMResponse:
        """异步聊天补全接口。

        Args:
            messages: 消息列表，格式为 [{"role": "user"/"system"/"assistant", "content": "..."}]
            **kwargs: 额外参数（如 temperature, max_tokens 覆盖）

        Returns:
            统一 LLMResponse 对象

        Raises:
            LLMAPIError: API 调用失败
            LLMTimeoutError: 请求超时
        """
        ...

    def is_mock(self) -> bool:
        """是否为 Mock Provider。

        Returns:
            True 表示 Mock 模式（零成本）
        """
        return self.name == "mock"
