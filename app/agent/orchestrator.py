"""Agent 编排器入口（T4-6/T4-7/T4-8/T4-9）。

整合状态图、节点、数据库持久化：
- 创建并配置状态图（Planner→Executor→Reviewer→Aggregator）
- 执行编排流程
- 持久化消息到 agent_messages 表
- 检查点管理（T4-7）
- 取消标志支持（T4-8）
- 最大轮次限制（T4-9）

架构决策（T-01）：编排器为无状态服务，
每次调用创建新的状态图实例，状态通过数据库和检查点持久化。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.agent.graph import StateGraph
from app.agent.nodes.aggregator import AggregatorNode
from app.agent.nodes.executor import ExecutorNode
from app.agent.nodes.planner import PlannerNode
from app.agent.nodes.reviewer import ReviewerNode
from app.agent.state import AgentState, create_initial_state
from app.config import get_config, get_config_paths
from app.core.logging import get_logger
from app.llm.gateway import LLMGateway
from app.models.base import async_session_factory
from app.models.task import AgentMessage, Task, TaskExecution
from app.repositories.task_repo import TaskRepository

logger = get_logger(__name__)


class AgentOrchestrator:
    """Agent 编排器（T4-6）。

    管理完整的 Agent 编排流程，包括状态图执行和数据库持久化。
    """

    def __init__(
        self,
        gateway: Optional[LLMGateway] = None,
        use_mcp: bool = True,
        checkpoint_enabled: bool = True,
    ) -> None:
        """初始化编排器。

        Args:
            gateway: LLM 网关
            use_mcp: 是否启用 MCP 工具
            checkpoint_enabled: 是否启用检查点（T4-7）
        """
        self.gateway = gateway or LLMGateway()
        self.use_mcp = use_mcp
        self.checkpoint_enabled = checkpoint_enabled
        self.config = get_config()
        self.paths = get_config_paths()

    def build_graph(self) -> StateGraph:
        """构建 Agent 状态图（T4-6）。

        流程：Planner → Executor → Reviewer → (条件路由) → Aggregator
        - Reviewer 未通过时回到 Executor 重做
        - 所有子任务完成后进入 Aggregator

        Returns:
            配置好的 StateGraph 实例
        """
        graph = StateGraph()

        # 创建节点实例
        planner = PlannerNode(gateway=self.gateway)
        executor = ExecutorNode(gateway=self.gateway, use_mcp=self.use_mcp)
        reviewer = ReviewerNode(gateway=self.gateway)
        aggregator = AggregatorNode(gateway=self.gateway)

        # 注册节点
        graph.add_node("planner", planner)
        graph.add_node("executor", executor)
        graph.add_node("reviewer", reviewer)
        graph.add_node("aggregator", aggregator)

        # 设置入口和终点
        graph.set_entry_point("planner")
        graph.set_finish_point("aggregator")

        # 固定边
        graph.add_edge("planner", "executor")
        graph.add_edge("executor", "reviewer")

        # 条件边：Reviewer 根据状态路由
        def route_after_review(state: AgentState) -> Optional[str]:
            """Reviewer 后的条件路由。

            - status=executing: 继续执行下一个子任务
            - status=aggregating: 所有子任务完成，进入汇总
            - 其他: 结束
            """
            status = state.get("status", "")
            if status == "executing":
                return "executor"
            elif status == "aggregating":
                return "aggregator"
            return None

        graph.add_conditional_edges("reviewer", route_after_review)

        return graph

    async def run(
        self,
        user_input: str,
        task_id: Optional[str] = None,
        trace_id: str = "",
        max_rounds: int = 5,
    ) -> AgentState:
        """执行完整的 Agent 编排流程。

        Args:
            user_input: 用户输入
            task_id: 任务 ID（为 None 时自动生成）
            trace_id: 链路追踪 ID
            max_rounds: 最大轮次数（T4-9）

        Returns:
            最终状态（含 final_output）
        """
        if task_id is None:
            task_id = str(uuid.uuid4())

        logger.info("orchestrator_run_start", task_id=task_id, input_length=len(user_input))

        # 创建初始状态
        state = create_initial_state(
            task_id=task_id,
            user_input=user_input,
            trace_id=trace_id,
            max_rounds=max_rounds,
        )

        # 检查点目录（T4-7）
        checkpoint_dir: Optional[Path] = None
        if self.checkpoint_enabled:
            checkpoint_dir = self.paths.get("checkpoints", Path("data/checkpoints")) / task_id
            checkpoint_dir.mkdir(parents=True, exist_ok=True)

        # 构建并执行状态图
        graph = self.build_graph()

        # 修复-BUG-014 读取DB取消标志同步到初始state
        cancellation_requested = await self._check_cancellation_flag(task_id)
        if cancellation_requested:
            state["cancelled"] = True
            logger.info("orchestrator_cancelled_before_start", task_id=task_id)

        final_state = await graph.ainvoke(
            initial_state=state,
            checkpoint_dir=checkpoint_dir,
            max_steps=50,
            cancel_check=lambda: self._check_cancellation_flag(task_id),  # 修复-BUG-014 传入异步取消检查回调
        )

        # 持久化消息到数据库
        await self._persist_messages(task_id, final_state)

        # 修复-BUG-003 持久化节点执行记录到task_executions表
        await self._persist_executions(task_id, final_state)

        logger.info(
            "orchestrator_run_complete",
            task_id=task_id,
            status=final_state.get("status"),
            output_length=len(final_state.get("final_output", "")),
        )

        return final_state

    async def _persist_messages(self, task_id: str, state: AgentState) -> None:
        """持久化 Agent 消息到数据库。

        修复-BUG-004/NEW-005:
        - 补充 agent_role/message_type/round_number 字段
        - sequence 改为轮次内序号（而非全局序号）

        Args:
            task_id: 任务 ID
            state: 最终状态（含 messages）
        """
        messages = state.get("messages", [])
        if not messages:
            return

        try:
            async with async_session_factory() as db:
                repo = TaskRepository(db)

                # 修复-BUG-004/NEW-005 按轮次分组，计算轮次内sequence
                message_objects = []
                round_counters: dict[int, int] = {}  # round_number -> 当前轮次内序号

                for msg in messages:
                    round_num = msg.get("round_number", state.get("round_count", 0))
                    if round_num is None:
                        round_num = 0

                    # 修复-NEW-005 sequence为轮次内消息序号
                    seq_in_round = round_counters.get(round_num, 0)
                    round_counters[round_num] = seq_in_round + 1

                    # 修复-BUG-004 推断agent_role：从node字段映射
                    node = msg.get("node", "")
                    agent_role = node if node in ("planner", "executor", "reviewer", "aggregator") else None

                    # 修复-BUG-004 推断message_type
                    message_type = msg.get("message_type", "text")
                    if msg.get("tool_calls") or "tool_call" in str(msg.get("content", "")).lower():
                        message_type = "tool_call"
                    elif msg.get("role") == "tool":
                        message_type = "tool_result"

                    message_objects.append(AgentMessage(
                        task_id=task_id,
                        role=msg.get("role", "assistant"),
                        agent_role=agent_role,  # 修复-BUG-004 补充发送方Agent角色
                        content=msg.get("content", "")[:5000],
                        message_type=message_type,  # 修复-BUG-004 补充消息类型
                        round_number=round_num,  # 修复-BUG-004 补充协作轮次
                        sequence=seq_in_round,  # 修复-NEW-005 轮次内序号
                    ))

                if message_objects:
                    await repo.bulk_insert_messages(message_objects)
                    await repo.commit()

                logger.info("messages_persisted", task_id=task_id, count=len(message_objects))

        except Exception as e:
            logger.error("message_persistence_failed", task_id=task_id, error=str(e))

    async def _persist_executions(self, task_id: str, state: AgentState) -> None:
        """修复-BUG-003 持久化节点执行记录到task_executions表。

        Args:
            task_id: 任务 ID
            state: 最终状态（含 node_executions）
        """
        node_executions = state.get("node_executions", [])
        if not node_executions:
            return

        try:
            async with async_session_factory() as db:
                repo = TaskRepository(db)

                execution_objects = []
                for exec_info in node_executions:
                    execution_objects.append(TaskExecution(
                        task_id=task_id,
                        agent_role=exec_info.get("agent_role", "unknown"),
                        node_name=exec_info.get("node_name", "unknown"),
                        input_data=exec_info.get("input_data", "{}"),
                        output_data=exec_info.get("output_data"),
                        round_number=exec_info.get("round_number", 0),
                        created_at=exec_info.get("created_at"),
                    ))

                if execution_objects:
                    await repo.bulk_insert_executions(execution_objects)
                    await repo.commit()

                logger.info("executions_persisted", task_id=task_id, count=len(execution_objects))

        except Exception as e:
            logger.error("execution_persistence_failed", task_id=task_id, error=str(e))

    async def _check_cancellation_flag(self, task_id: str) -> bool:
        """修复-BUG-014 查询DB中的取消请求标志。

        Args:
            task_id: 任务 ID

        Returns:
            True 表示已请求取消
        """
        try:
            async with async_session_factory() as db:
                repo = TaskRepository(db)
                task = await repo.get_by_id(task_id)
                if task and task.cancellation_requested:
                    return True
        except Exception as e:
            logger.warning("check_cancellation_flag_failed", task_id=task_id, error=str(e))
        return False

    @staticmethod
    def get_checkpoint_dir(task_id: str) -> Path:
        """获取任务的检查点目录路径。

        Args:
            task_id: 任务 ID

        Returns:
            检查点目录路径
        """
        return Path("data/checkpoints") / task_id

    @staticmethod
    def list_checkpoints(task_id: str) -> list[Path]:
        """列出任务的所有检查点文件。

        Args:
            task_id: 任务 ID

        Returns:
            检查点文件路径列表（按时间排序）
        """
        checkpoint_dir = Path("data/checkpoints") / task_id
        if not checkpoint_dir.exists():
            return []
        return sorted(checkpoint_dir.glob("checkpoint_*.json"))
