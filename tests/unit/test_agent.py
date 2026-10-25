"""Agent 编排引擎单元测试（M12）。

覆盖：状态模型、状态图引擎、Planner/Executor/Reviewer/Aggregator 节点。
"""

from __future__ import annotations

import pytest

from app.agent.graph import StateGraph
from app.agent.state import AgentState, create_initial_state


class TestAgentState:
    """Agent 状态模型测试（T4-1）。"""

    def test_create_initial_state(self) -> None:
        """测试创建初始状态。"""
        state = create_initial_state(task_id="test-001", user_input="test input")
        assert state["task_id"] == "test-001"
        assert state["user_input"] == "test input"
        assert state["status"] == "planning"
        assert state["cancelled"] is False
        assert state["round_count"] == 0
        assert len(state["messages"]) == 1

    def test_state_fields_optional(self) -> None:
        """测试状态字段可选（TypedDict total=False）。"""
        state: AgentState = {"task_id": "test", "user_input": "test"}
        assert "subtasks" not in state
        assert "final_output" not in state


class TestStateGraph:
    """轻量级状态图引擎测试（T4-6）。"""

    @pytest.mark.asyncio
    async def test_simple_linear_flow(self) -> None:
        """测试简单线性流程。"""
        graph = StateGraph()

        async def node_a(state: AgentState) -> dict:
            return {"value": "a", "status": "next"}

        async def node_b(state: AgentState) -> dict:
            return {"value": state.get("value", "") + "b", "status": "done"}

        graph.add_node("a", node_a)
        graph.add_node("b", node_b)
        graph.set_entry_point("a")
        graph.set_finish_point("b")
        graph.add_edge("a", "b")

        result = await graph.ainvoke(create_initial_state("test", "test"))
        assert result["value"] == "ab"
        assert result["status"] == "done"

    @pytest.mark.asyncio
    async def test_conditional_routing(self) -> None:
        """测试条件路由。"""
        graph = StateGraph()

        async def start(state: AgentState) -> dict:
            return {"go_to": "b"}

        async def node_a(state: AgentState) -> dict:
            return {"visited": "a"}

        async def node_b(state: AgentState) -> dict:
            return {"visited": "b"}

        graph.add_node("start", start)
        graph.add_node("a", node_a)
        graph.add_node("b", node_b)
        graph.set_entry_point("start")
        graph.set_finish_point("a")
        graph.set_finish_point("b")

        def route(state: AgentState) -> str | None:
            return state.get("go_to", "a")

        graph.add_conditional_edges("start", route)

        result = await graph.ainvoke(create_initial_state("test", "test"))
        assert result["visited"] == "b"

    @pytest.mark.asyncio
    async def test_cancellation_stops_execution(self) -> None:
        """测试取消标志停止执行（T4-8）。"""
        graph = StateGraph()

        async def set_cancel(state: AgentState) -> dict:
            return {"cancelled": True}

        async def should_not_run(state: AgentState) -> dict:
            return {"ran": True}

        graph.add_node("cancel", set_cancel)
        graph.add_node("next", should_not_run)
        graph.set_entry_point("cancel")
        graph.set_finish_point("next")
        graph.add_edge("cancel", "next")

        result = await graph.ainvoke(create_initial_state("test", "test"))
        assert result["status"] == "cancelled"
        assert "ran" not in result

    @pytest.mark.asyncio
    async def test_max_rounds_limit(self) -> None:
        """测试最大轮次限制（T4-9）。"""
        graph = StateGraph()

        call_count = 0

        async def loop_node(state: AgentState) -> dict:
            nonlocal call_count
            call_count += 1
            return {"round_count": state.get("round_count", 0) + 1, "status": "executing"}

        graph.add_node("loop", loop_node)
        graph.set_entry_point("loop")
        graph.add_edge("loop", "loop")  # 自循环

        result = await graph.ainvoke(
            create_initial_state("test", "test", max_rounds=3)
        )
        assert result["status"] == "failed"
        assert "最大轮次" in result.get("error", "")


class TestPlannerNode:
    """Planner 节点测试（T4-2）。"""

    @pytest.mark.asyncio
    async def test_planner_returns_subtasks(self) -> None:
        """测试 Planner 返回子任务列表（Mock 模式）。"""
        from app.agent.nodes.planner import PlannerNode

        node = PlannerNode()
        state = create_initial_state("test-001", "测试任务")
        result = await node(state)
        assert "subtasks" in result
        assert len(result["subtasks"]) > 0
        assert result["status"] == "executing"


class TestAggregatorNode:
    """Aggregator 节点测试（T4-5）。"""

    @pytest.mark.asyncio
    async def test_aggregator_returns_output(self) -> None:
        """测试 Aggregator 返回最终输出。"""
        from app.agent.nodes.aggregator import AggregatorNode

        node = AggregatorNode()
        state = create_initial_state("test-001", "测试任务")
        state["subtasks"] = [{"id": "s1", "description": "子任务1", "tool": "none", "depends_on": []}]
        state["subtask_results"] = {"s1": {"subtask_id": "s1", "status": "success", "output": "结果1", "tool_calls": [], "error": None}}

        result = await node(state)
        assert "final_output" in result
        assert result["status"] == "completed"
