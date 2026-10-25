"""MCP 工具注册中心（T3-1）。

定义 ToolAdapter Protocol 和工具注册中心：
- ToolAdapter：工具适配器协议，所有工具必须实现 name/description/input_schema/execute
- ToolRegistry：工具注册与发现，新增工具仅需注册不修改核心代码（T-03 ≤50行）

架构决策（T-03）：通过注册中心实现工具的插件化，
新增工具只需实现 ToolAdapter 并调用 register()，不修改核心代码。
"""

from __future__ import annotations

from typing import Any, Callable, Protocol, runtime_checkable

from app.core.exceptions import AppException, ErrorCode
from app.core.logging import get_logger

logger = get_logger(__name__)


@runtime_checkable
class ToolAdapter(Protocol):
    """工具适配器协议（T3-1）。

    所有 MCP 工具必须实现此协议。新增工具只需实现以下属性和方法。

    Attributes:
        name: 工具唯一名称（如 web_search、code_exec）
        description: 工具描述（用于 LLM 理解工具用途）
        input_schema: JSON Schema 定义的输入参数结构
    """

    name: str
    description: str
    input_schema: dict[str, Any]

    def execute(self, arguments: dict[str, Any]) -> str:
        """同步执行工具（MCP stdio 为同步协议）。

        Args:
            arguments: 工具参数字典，需符合 input_schema

        Returns:
            工具执行结果的文本内容

        Raises:
            AppException: 工具执行失败时抛出
        """
        ...


class ToolRegistry:
    """工具注册中心（T3-1）。

    全局单例，管理所有已注册的工具适配器。
    支持注册、查询、列表、调用。
    """

    def __init__(self) -> None:
        """初始化空注册中心。"""
        self._tools: dict[str, ToolAdapter] = {}

    def register(self, tool: ToolAdapter) -> None:
        """注册工具适配器。

        Args:
            tool: 工具适配器实例

        Raises:
            ValueError: 工具名重复时抛出
        """
        if tool.name in self._tools:
            raise ValueError(f"工具 {tool.name} 已注册")
        self._tools[tool.name] = tool
        logger.info("tool_registered", tool_name=tool.name)

    def unregister(self, tool_name: str) -> None:
        """注销工具。

        Args:
            tool_name: 工具名称
        """
        if tool_name in self._tools:
            del self._tools[tool_name]
            logger.info("tool_unregistered", tool_name=tool_name)

    def get(self, tool_name: str) -> ToolAdapter:
        """获取工具适配器。

        Args:
            tool_name: 工具名称

        Returns:
            工具适配器实例

        Raises:
            AppException(TOOL_NOT_FOUND): 工具未注册时抛出
        """
        tool = self._tools.get(tool_name)
        if tool is None:
            raise AppException(
                ErrorCode.TOOL_NOT_FOUND,
                f"MCP 工具 {tool_name} 不存在",
                details={"tool_name": tool_name, "available_tools": list(self._tools.keys())},
            )
        return tool

    def list_tools(self) -> list[dict[str, Any]]:
        """列出全部已注册工具的元信息。

        Returns:
            工具元信息列表，每项含 name/description/input_schema
        """
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in self._tools.values()
        ]

    def has(self, tool_name: str) -> bool:
        """检查工具是否已注册。

        Args:
            tool_name: 工具名称

        Returns:
            True 表示已注册
        """
        return tool_name in self._tools

    @property
    def tool_names(self) -> list[str]:
        """获取全部工具名称列表。"""
        return list(self._tools.keys())


# 全局注册中心单例
_registry: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    """获取全局工具注册中心单例。

    Returns:
        ToolRegistry 实例
    """
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry


def register_tool(tool: ToolAdapter) -> None:
    """便捷函数：注册工具到全局注册中心。

    Args:
        tool: 工具适配器实例
    """
    get_registry().register(tool)


def validate_arguments(tool: ToolAdapter, arguments: dict[str, Any]) -> dict[str, Any]:
    """JSON Schema 参数校验（T3-8，T-04 要求 100% 覆盖）。

    所有工具调用前执行参数校验，校验失败返回 40002 错误。
    支持 required、type、enum、minimum/maximum、minLength/maxLength 等基础校验。

    Args:
        tool: 工具适配器
        arguments: 待校验的参数字典

    Returns:
        校验通过的参数字典（可能填充了默认值）

    Raises:
        AppException(TOOL_VALIDATION_ERROR): 参数校验失败时抛出
    """
    schema = tool.input_schema
    properties = schema.get("properties", {})
    required = schema.get("required", [])

    errors: list[str] = []

    # 检查必填字段
    for field in required:
        if field not in arguments or arguments[field] is None:
            errors.append(f"缺少必填参数: {field}")

    # 检查每个字段的类型和约束
    for field, value in arguments.items():
        if field not in properties:
            continue  # 允许额外字段，不报错

        field_schema = properties[field]
        expected_type = field_schema.get("type")

        # 类型校验
        if expected_type and value is not None:
            type_map = {
                "string": str,
                "integer": int,
                "number": (int, float),
                "boolean": bool,
                "array": list,
                "object": dict,
            }
            expected_py_type = type_map.get(expected_type)
            if expected_py_type and not isinstance(value, expected_py_type):
                # bool 是 int 的子类，需要特殊处理
                if expected_type == "integer" and isinstance(value, bool):
                    errors.append(f"参数 {field} 类型错误: 期望 integer, 实际 boolean")
                elif expected_type != "number" or not isinstance(value, (int, float)):
                    errors.append(
                        f"参数 {field} 类型错误: 期望 {expected_type}, 实际 {type(value).__name__}"
                    )

        # 字符串约束
        if isinstance(value, str):
            if "minLength" in field_schema and len(value) < field_schema["minLength"]:
                errors.append(f"参数 {field} 长度不足: 最小 {field_schema['minLength']}")
            if "maxLength" in field_schema and len(value) > field_schema["maxLength"]:
                errors.append(f"参数 {field} 长度超限: 最大 {field_schema['maxLength']}")

        # 数值约束
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in field_schema and value < field_schema["minimum"]:
                errors.append(f"参数 {field} 值过小: 最小 {field_schema['minimum']}")
            if "maximum" in field_schema and value > field_schema["maximum"]:
                errors.append(f"参数 {field} 值过大: 最大 {field_schema['maximum']}")

        # 枚举约束
        if "enum" in field_schema and value is not None and value not in field_schema["enum"]:
            errors.append(f"参数 {field} 值无效: 可选值 {field_schema['enum']}")

    if errors:
        logger.warning("tool_validation_failed", tool=tool.name, errors=errors)
        raise AppException(
            ErrorCode.TOOL_VALIDATION_ERROR,
            f"工具 {tool.name} 参数校验失败",
            details={"errors": errors, "received": arguments},
        )

    return arguments
