"""轻量级状态图引擎（T4-6）。

兼容 LangGraph 核心概念的轻量级实现：
- StateGraph：状态图，支持节点注册和条件边
- 节点：异步函数，接收 state 返回 state 更新
- 条件边：根据 state 决定下一个节点
- 检查点：每步执行后保存状态，支持回放恢复（T4-7）

架构决策：不依赖 langgraph 0.2.39 的具体 API，
实现轻量级版本确保稳定性和可测试性。
核心概念与 LangGraph 兼容，便于未来迁移。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from app.agent.state import AgentState
from app.core.logging import get_logger

logger = get_logger(__name__)

# 节点函数类型：接收 state，返回 state 更新字典
NodeFn = Callable[[AgentState], Awaitable[dict[str, Any]]]
# 条件路由函数：接收 state，返回下一个节点名或 None（结束）
ConditionalFn = Callable[[AgentState], Optional[str]]


class StateGraph:
    """轻量级状态图（T4-6）。

    支持节点注册、条件边、顺序执行、检查点。
    """

    def __init__(self, state_type: type = AgentState) -> None:
        """初始化状态图。

        Args:
            state_type: 状态类型（默认 AgentState）
        """
        self.state_type = state_type
        self._nodes: dict[str, NodeFn] = {}
        self._edges: dict[str, str] = {}  # 固定边：from -> to
        self._conditional_edges: dict[str, ConditionalFn] = {}  # 条件边
        self._entry_point: Optional[str] = None
        self._finish_nodes: set[str] = set()

    def add_node(self, name: str, fn: NodeFn) -> None:
        """注册节点。

        Args:
            name: 节点名称
            fn: 节点函数（异步）
        """
        self._nodes[name] = fn
        logger.debug("graph_add_node", name=name)

    def add_edge(self, from_node: str, to_node: str) -> None:
        """添加固定边。

        Args:
            from_node: 源节点
            to_node: 目标节点
        """
        self._edges[from_node] = to_node

    def add_conditional_edges(self, from_node: str, fn: ConditionalFn) -> None:
        """添加条件边。

        Args:
            from_node: 源节点
            fn: 条件路由函数，返回下一个节点名或 None（结束）
        """
        self._conditional_edges[from_node] = fn

    def set_entry_point(self, name: str) -> None:
        """设置入口节点。

        Args:
            name: 入口节点名称
        """
        self._entry_point = name

    def set_finish_point(self, name: str) -> None:
        """设置终点节点（执行到此节点后结束）。

        Args:
            name: 终点节点名称
        """
        self._finish_nodes.add(name)

    async def ainvoke(
        self,
        initial_state: AgentState,
        checkpoint_dir: Optional[Path] = None,
        max_steps: int = 50,
        cancel_check: Optional[Callable[[], Awaitable[bool]]] = None,
    ) -> AgentState:
        """异步执行状态图。

        Args:
            initial_state: 初始状态
            checkpoint_dir: 检查点目录（T4-7，为 None 时不保存检查点）
            max_steps: 最大执行步数（防止死循环）
            cancel_check: 取消检查回调（修复-BUG-014 每步执行前调用，返回True表示应取消）

        Returns:
            最终状态

        Raises:
            RuntimeError: 入口节点未设置或超过最大步数时抛出
        """
        if self._entry_point is None:
            raise RuntimeError("状态图入口节点未设置")

        state = dict(initial_state)
        current_node = self._entry_point
        steps = 0

        # 创建检查点目录
        if checkpoint_dir:
            checkpoint_dir.mkdir(parents=True, exist_ok=True)

        while current_node and steps < max_steps:
            steps += 1

            # 检查取消标志（T4-8）
            if state.get("cancelled", False):
                logger.info("graph_cancelled", node=current_node, steps=steps)
                state["status"] = "cancelled"
                state["error"] = "任务被用户取消"
                break

            # 修复-BUG-014 每步执行前通过异步回调检查DB取消标志
            if cancel_check is not None:
                try:
                    if await cancel_check():
                        logger.info("graph_cancelled_by_db_flag", node=current_node, steps=steps)
                        state["cancelled"] = True
                        state["status"] = "cancelled"
                        state["error"] = "任务被用户取消"
                        break
                except Exception as e:
                    logger.warning("cancel_check_failed", node=current_node, error=str(e))

            # 检查最大轮次（T4-9）
            if state.get("round_count", 0) > state.get("max_rounds", 5):
                logger.warning("graph_max_rounds_exceeded", node=current_node)
                state["status"] = "failed"
                state["error"] = f"超过最大轮次限制（{state.get('max_rounds', 5)}）"
                break

            # 执行节点
            node_fn = self._nodes.get(current_node)
            if node_fn is None:
                logger.error("graph_node_not_found", node=current_node)
                state["status"] = "failed"
                state["error"] = f"节点 {current_node} 不存在"
                break

            logger.info("graph_node_execute", node=current_node, step=steps)

            # 修复-BUG-003 记录节点执行开始时间和输入
            node_start_time = datetime.now(timezone.utc).replace(tzinfo=None)
            node_input_snapshot = self._snapshot_state_for_execution(state, current_node)

            try:
                update = await node_fn(state)
                # 合并状态更新
                state.update(update)

                # 修复-BUG-003 收集节点执行记录到state
                node_executions = state.get("node_executions", [])
                node_executions.append({
                    "node_name": current_node,
                    "agent_role": current_node,  # planner/executor/reviewer/aggregator
                    "input_data": json.dumps(node_input_snapshot, ensure_ascii=False, default=str)[:5000],
                    "output_data": json.dumps(update, ensure_ascii=False, default=str)[:5000],
                    "round_number": state.get("round_count", 0),
                    "created_at": node_start_time,
                })
                state["node_executions"] = node_executions

            except Exception as e:
                logger.error("graph_node_error", node=current_node, error=str(e))
                state["status"] = "failed"
                state["error"] = f"节点 {current_node} 执行失败: {e}"

                # 修复-BUG-003 记录失败的节点执行
                node_executions = state.get("node_executions", [])
                node_executions.append({
                    "node_name": current_node,
                    "agent_role": current_node,
                    "input_data": json.dumps(node_input_snapshot, ensure_ascii=False, default=str)[:5000],
                    "output_data": "",
                    "round_number": state.get("round_count", 0),
                    "created_at": node_start_time,
                })
                state["node_executions"] = node_executions
                break

            # 保存检查点（T4-7）
            if checkpoint_dir:
                self._save_checkpoint(checkpoint_dir, steps, current_node, state)

            # 判断是否到达终点
            if current_node in self._finish_nodes:
                logger.info("graph_finish_node_reached", node=current_node)
                break

            # 路由到下一个节点
            current_node = self._route(current_node, state)

        if steps >= max_steps:
            logger.warning("graph_max_steps_exceeded", steps=steps)
            state["status"] = "failed"
            state["error"] = f"超过最大执行步数（{max_steps}）"

        logger.info("graph_complete", steps=steps, status=state.get("status"))
        return state

    def _route(self, current_node: str, state: AgentState) -> Optional[str]:
        """根据边和条件路由到下一个节点。

        Args:
            current_node: 当前节点
            state: 当前状态

        Returns:
            下一个节点名称，None 表示结束
        """
        # 优先条件边
        if current_node in self._conditional_edges:
            next_node = self._conditional_edges[current_node](state)
            logger.debug("graph_conditional_route", from_node=current_node, to_node=next_node)
            return next_node

        # 固定边
        if current_node in self._edges:
            return self._edges[current_node]

        return None

    @staticmethod
    def _snapshot_state_for_execution(state: AgentState, node_name: str) -> dict[str, Any]:
        """修复-BUG-003 为节点执行记录生成状态快照（排除大字段）。

        Args:
            state: 当前状态
            node_name: 节点名称

        Returns:
            可序列化的状态快照字典
        """
        snapshot = {}
        for key, value in state.items():
            # 排除消息列表和执行记录本身（避免无限嵌套和过大）
            if key in ("messages", "node_executions"):
                continue
            try:
                json.dumps(value, default=str)
                snapshot[key] = value
            except (TypeError, ValueError):
                snapshot[key] = str(value)
        snapshot["_node"] = node_name
        return snapshot

    @staticmethod
    def _save_checkpoint(checkpoint_dir: Path, step: int, node: str, state: AgentState) -> None:
        """保存检查点（T4-7）。

        Args:
            checkpoint_dir: 检查点目录
            step: 当前步数
            node: 当前节点
            state: 当前状态
        """
        try:
            checkpoint_file = checkpoint_dir / f"checkpoint_{step:04d}_{node}.json"
            # 序列化状态（处理不可序列化的对象）
            serializable_state = {}
            for key, value in state.items():
                try:
                    json.dumps(value)
                    serializable_state[key] = value
                except (TypeError, ValueError):
                    serializable_state[key] = str(value)

            checkpoint_file.write_text(
                json.dumps(serializable_state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("checkpoint_save_failed", step=step, error=str(e))

    @staticmethod
    def load_checkpoint(checkpoint_file: Path) -> AgentState:
        """加载检查点（T4-7，回放恢复）。

        Args:
            checkpoint_file: 检查点文件路径

        Returns:
            恢复的状态
        """
        data = json.loads(checkpoint_file.read_text(encoding="utf-8"))
        return data  # type: ignore[return-value]
