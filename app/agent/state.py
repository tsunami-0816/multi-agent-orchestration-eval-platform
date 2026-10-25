"""Agent 状态模型（T4-1）。

定义 LangGraph 兼容的 TypedDict 状态，在节点间传递：
- task_id: 任务 ID
- user_input: 用户原始输入
- subtasks: Planner 分解的子任务列表
- current_subtask_index: 当前执行的子任务索引
- subtask_results: 各子任务执行结果
- review_results: 审查结果
- final_output: 最终汇总输出
- messages: 完整对话历史（用于持久化和回放）
- round_count: 当前轮次（用于最大轮次限制 T4-9）
- cancelled: 取消标志（T4-8）
- error: 错误信息

架构决策（T-01）：使用 TypedDict 而非 Pydantic，
与 LangGraph 状态定义兼容，轻量且无需额外依赖。
"""

from __future__ import annotations

from typing import Any, TypedDict


class SubTask(TypedDict):
    """子任务定义（Planner 输出）。"""

    id: str
    """子任务唯一 ID（如 subtask-001）"""
    description: str
    """子任务描述"""
    tool: str
    """推荐使用的 MCP 工具（web_search/code_exec/file_rw/db_query/none）"""
    depends_on: list[str]
    """依赖的子任务 ID 列表"""


class SubTaskResult(TypedDict):
    """子任务执行结果（Executor 输出）。"""

    subtask_id: str
    """子任务 ID"""
    status: str
    """执行状态：success/failed/skipped"""
    output: str
    """执行输出文本"""
    tool_calls: list[dict[str, Any]]
    """工具调用记录"""
    error: str | None
    """错误信息（失败时）"""


class ReviewResult(TypedDict):
    """审查结果（Reviewer 输出）。"""

    subtask_id: str
    """子任务 ID"""
    passed: bool
    """是否通过审查"""
    feedback: str
    """审查反馈/修改建议"""
    score: float
    """质量评分（0-10）"""


class AgentState(TypedDict, total=False):
    """Agent 编排状态（T4-1）。

    在 LangGraph 节点间传递的完整状态。
    total=False 表示所有字段可选，便于节点逐步填充。
    """

    task_id: str
    """任务 ID（用于持久化和关联）"""

    user_input: str
    """用户原始输入"""

    subtasks: list[SubTask]
    """Planner 分解的子任务列表"""

    current_subtask_index: int
    """当前执行的子任务索引（从 0 开始）"""

    subtask_results: dict[str, SubTaskResult]
    """各子任务执行结果（key=subtask_id）"""

    review_results: dict[str, ReviewResult]
    """各子任务审查结果（key=subtask_id）"""

    final_output: str
    """最终汇总输出（Aggregator 输出）"""

    messages: list[dict[str, Any]]
    """完整对话历史（用于持久化和回放恢复 T4-7）"""

    node_executions: list[dict[str, Any]]
    """节点执行记录（修复-BUG-003 收集每个节点的输入输出，用于task_executions表持久化）"""

    round_count: int
    """当前轮次（用于最大轮次限制 T4-9）"""

    max_rounds: int
    """最大轮次数（默认 5）"""

    cancelled: bool
    """取消标志（T4-8，外部设置为 True 时停止执行）"""

    error: str | None
    """错误信息（执行失败时）"""

    status: str
    """整体状态：planning/executing/reviewing/aggregating/completed/failed/cancelled"""

    trace_id: str
    """链路追踪 ID"""


def create_initial_state(task_id: str, user_input: str, trace_id: str = "", max_rounds: int = 5) -> AgentState:
    """创建初始状态。

    Args:
        task_id: 任务 ID
        user_input: 用户输入
        trace_id: 链路追踪 ID
        max_rounds: 最大轮次数

    Returns:
        初始化的 AgentState
    """
    return {
        "task_id": task_id,
        "user_input": user_input,
        "subtasks": [],
        "current_subtask_index": 0,
        "subtask_results": {},
        "review_results": {},
        "final_output": "",
        "messages": [{"role": "user", "content": user_input}],
        "node_executions": [],  # 修复-BUG-003 初始化节点执行记录收集列表
        "round_count": 0,
        "max_rounds": max_rounds,
        "cancelled": False,
        "error": None,
        "status": "planning",
        "trace_id": trace_id,
    }
