"""数据模型与 Repository 单元测试（T1-2/T1-3/T1-5）。

测试 ORM 模型创建、CRUD 操作、原子递增、分页查询、批量插入。
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest

from app.models.eval import EvalCase, EvalReport, EvalRun, EvalScore
from app.models.task import AgentMessage, Task, TaskExecution, ToolCall
from app.models.usage import LLMUsage


class TestTaskModel:
    """任务模型测试。"""

    @pytest.mark.asyncio
    async def test_create_task(self, task_repo) -> None:
        """测试创建任务。"""
        task_id = str(uuid.uuid4())
        task = Task(
            id=task_id,
            input="测试任务输入",
            scenario="research",
            status="PENDING",
            max_rounds=5,
        )
        created = await task_repo.create(task)
        assert created.id == task_id
        assert created.status == "PENDING"
        assert created.scenario == "research"
        assert created.cancellation_requested is False

    @pytest.mark.asyncio
    async def test_get_task_by_id(self, task_repo) -> None:
        """测试根据 ID 查询任务。"""
        task_id = str(uuid.uuid4())
        task = Task(id=task_id, input="测试", scenario="default")
        await task_repo.create(task)

        found = await task_repo.get_by_id(task_id)
        assert found is not None
        assert found.id == task_id

        not_found = await task_repo.get_by_id("nonexistent")
        assert not_found is None

    @pytest.mark.asyncio
    async def test_update_task_status(self, task_repo) -> None:
        """测试更新任务状态。"""
        task_id = str(uuid.uuid4())
        task = Task(id=task_id, input="测试", scenario="default")
        await task_repo.create(task)

        # 修复-OUT-003 改动原因：task_repo.update_status 内部使用小写状态判断时间戳设置，
        # 原测试用大写 "RUNNING"/"COMPLETED" 导致 started_at/completed_at 不被赋值而断言失败
        # pending -> running
        updated = await task_repo.update_status(task_id, "running")
        assert updated.status == "running"
        assert updated.started_at is not None

        # running -> completed
        updated = await task_repo.update_status(task_id, "completed", result="测试结果")
        assert updated.status == "completed"
        assert updated.result == "测试结果"
        assert updated.completed_at is not None
        assert updated.duration_seconds is not None

    @pytest.mark.asyncio
    async def test_set_cancellation_flag(self, task_repo) -> None:
        """测试设置取消标志（P1-4）。"""
        task_id = str(uuid.uuid4())
        task = Task(id=task_id, input="测试", scenario="default")
        await task_repo.create(task)

        result = await task_repo.set_cancellation_flag(task_id)
        assert result is True

        found = await task_repo.get_by_id(task_id)
        assert found.cancellation_requested is True

    @pytest.mark.asyncio
    async def test_list_paginated(self, task_repo) -> None:
        """测试分页查询任务列表。"""
        # 创建 5 个任务
        for i in range(5):
            task = Task(
                id=str(uuid.uuid4()),
                input=f"任务{i}",
                scenario="research" if i % 2 == 0 else "general",
                status="COMPLETED" if i < 3 else "PENDING",
            )
            await task_repo.create(task)
        await task_repo.commit()

        # 全量分页
        tasks, total = await task_repo.list_paginated(page=1, page_size=10)
        assert total == 5
        assert len(tasks) == 5

        # 按状态筛选
        tasks, total = await task_repo.list_paginated(status="COMPLETED")
        assert total == 3

        # 按场景筛选
        tasks, total = await task_repo.list_paginated(scenario="research")
        assert total == 3

    @pytest.mark.asyncio
    async def test_bulk_insert_messages(self, task_repo) -> None:
        """测试批量插入 Agent 消息。"""
        task_id = str(uuid.uuid4())
        task = Task(id=task_id, input="测试", scenario="default")
        await task_repo.create(task)

        messages = [
            AgentMessage(
                task_id=task_id,
                role="user",
                agent_role="planner",
                content="消息1",
                round_number=1,
                sequence=i,
            )
            for i in range(3)
        ]
        await task_repo.bulk_insert_messages(messages)
        await task_repo.commit()

        # 验证 trace 查询
        msgs, total = await task_repo.get_trace_messages(task_id)
        assert total == 3
        assert len(msgs) == 3


class TestEvalModel:
    """评测模型测试。"""

    @pytest.mark.asyncio
    async def test_upsert_case_idempotent(self, eval_repo) -> None:
        """测试用例幂等导入（P2-8，INSERT OR IGNORE）。"""
        case = EvalCase(
            id="case-001",
            category="research",
            input="测试用例输入",
            scoring_criteria="准确性、完整性",
            difficulty=2,
            enabled=True,
        )

        # 第一次插入
        inserted = await eval_repo.upsert_case(case)
        assert inserted is True

        # 重复插入（应被跳过）
        inserted = await eval_repo.upsert_case(case)
        assert inserted is False

        # 验证只有一条记录
        cases = await eval_repo.get_cases()
        assert len(cases) == 1

    @pytest.mark.asyncio
    async def test_increment_completed_cases_atomic(self, eval_repo) -> None:
        """测试 completed_cases 原子递增（P0-1）。"""
        run_id = str(uuid.uuid4())
        run = EvalRun(
            id=run_id,
            name="测试运行",
            model_config='{"model": "test"}',
            total_cases=10,
        )
        await eval_repo.create_run(run)

        # 递增 3 次，2 次通过
        await eval_repo.increment_completed_cases(run_id, passed=True)
        await eval_repo.increment_completed_cases(run_id, passed=False)
        await eval_repo.increment_completed_cases(run_id, passed=True)
        await eval_repo.commit()

        run = await eval_repo.get_run_by_id(run_id)
        assert run.completed_cases == 3
        assert run.passed_cases == 2

    @pytest.mark.asyncio
    async def test_insert_and_get_score(self, eval_repo) -> None:
        """测试评分记录插入和查询。"""
        # 先创建用例和运行
        case = EvalCase(
            id="case-score-001",
            category="general",
            input="测试",
            scoring_criteria="测试",
            difficulty=1,
            enabled=True,
        )
        await eval_repo.upsert_case(case)

        run_id = str(uuid.uuid4())
        run = EvalRun(id=run_id, name="测试", model_config="{}", total_cases=1)
        await eval_repo.create_run(run)

        # 插入评分
        score = EvalScore(
            run_id=run_id,
            case_id="case-score-001",
            accuracy_score=8.5,
            relevance_score=8.0,
            completeness_score=7.5,
            format_score=9.0,
            overall_score=8.2,
            judge_feedback="良好",
        )
        await eval_repo.insert_score(score)
        await eval_repo.commit()

        scores = await eval_repo.get_scores_by_run(run_id)
        assert len(scores) == 1
        assert scores[0].overall_score == 8.2


class TestUsageModel:
    """LLM 用量模型测试。"""

    @pytest.mark.asyncio
    async def test_insert_and_query_daily_cost(self, usage_repo) -> None:
        """测试用量记录插入和当日费用查询。"""
        await usage_repo.insert_usage(
            model="deepseek-chat",
            input_tokens=1000,
            output_tokens=500,
            cost_yuan=0.002,
        )
        await usage_repo.insert_usage(
            model="deepseek-chat",
            input_tokens=2000,
            output_tokens=1000,
            cost_yuan=0.004,
        )
        await usage_repo.commit()

        daily_cost = await usage_repo.get_daily_cost()
        assert daily_cost == 0.006

    @pytest.mark.asyncio
    async def test_daily_usage_by_model(self, usage_repo) -> None:
        """测试按模型分组统计。"""
        await usage_repo.insert_usage(model="model-a", input_tokens=100, output_tokens=50, cost_yuan=0.001)
        await usage_repo.insert_usage(model="model-b", input_tokens=200, output_tokens=100, cost_yuan=0.002)
        await usage_repo.commit()

        stats = await usage_repo.get_daily_usage_by_model()
        assert len(stats) == 2
        models = {s["model"] for s in stats}
        assert "model-a" in models
        assert "model-b" in models
