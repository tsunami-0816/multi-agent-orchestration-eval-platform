"""评测用例任务（T5-8）。

Celery 任务：异步执行单条评测用例。
- 输入：case_id + run_id + model_config
- 输出：评分结果（写入 eval_scores 表）
- 支持批量并发执行
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from app.agent.orchestrator import AgentOrchestrator
from app.core.logging import get_logger
from app.eval.judge import EvalJudge
from app.eval.scorer import EvalScorer
from app.models.base import async_session_factory
from app.models.eval import EvalCase, EvalRun, EvalScore
from app.models.task import Task
from app.repositories.eval_repo import EvalRepository
from app.repositories.task_repo import TaskRepository
from app.workers.celery_app import celery_app, is_celery_enabled

logger = get_logger(__name__)


def _run_eval_case_sync(run_id: str, case_id: str) -> dict[str, Any]:
    """同步执行单条评测用例。

    Args:
        run_id: 评测运行 ID
        case_id: 用例 ID

    Returns:
        评测结果字典
    """
    logger.info("eval_case_task_start", run_id=run_id, case_id=case_id)

    try:
        result = asyncio.run(_execute_eval_case(run_id, case_id))
        logger.info("eval_case_task_complete", run_id=run_id, case_id=case_id, score=result.get("overall_score"))
        return result
    except Exception as e:
        logger.error("eval_case_task_failed", run_id=run_id, case_id=case_id, error=str(e))
        # 修复-OUT-006 改动原因：_execute_eval_case 抛出未捕获异常时，原代码仅记录日志不递增 completed_cases，
        # 导致该用例永远不计入完成数，运行可能永久停留 running 状态。
        # 在异常分支中强制递增 completed_cases（failed=True），保证运行能正常推进到 completed。
        try:
            asyncio.run(_increment_completed_on_failure(run_id))
        except Exception as inc_err:
            logger.error("eval_case_increment_failed", run_id=run_id, case_id=case_id, error=str(inc_err))
        return {"run_id": run_id, "case_id": case_id, "status": "failed", "error": str(e)}


async def _increment_completed_on_failure(run_id: str) -> None:
    """修复-OUT-006 异常分支下递增 completed_cases（标记为 failed）。

    当 _execute_eval_case 抛出未捕获异常时调用，确保运行计数正确推进。
    """
    async with async_session_factory() as db:
        repo = EvalRepository(db)
        await repo.increment_completed_cases(run_id, passed=False, failed=True)
        await repo.commit()
    # 递增后尝试触发完成检测
    try:
        from app.eval.eval_service import EvalService
        eval_service = EvalService()
        await eval_service.check_completion(run_id)
    except Exception as e:
        logger.error("eval_check_completion_failed_after_error", run_id=run_id, error=str(e))


async def _execute_eval_case(run_id: str, case_id: str) -> dict[str, Any]:
    """异步执行单条评测用例。

    修复-BUG-007: 接入实际Agent编排执行，替换硬编码占位字符串
    修复-BUG-008: 创建关联Task记录，eval_scores.task_id关联
    修复-BUG-009: 完成后调用check_completion触发报告生成

    Args:
        run_id: 评测运行 ID
        case_id: 用例 ID

    Returns:
        评测结果
    """
    async with async_session_factory() as db:
        repo = EvalRepository(db)

        # 获取用例和运行信息
        case = await repo.get_case_by_id(case_id)
        run = await repo.get_run_by_id(run_id)

        if not case or not run:
            return {"run_id": run_id, "case_id": case_id, "status": "failed", "error": "用例或运行不存在"}

        # 修复-BUG-008 创建关联的Agent任务记录
        task_id = str(uuid.uuid4())
        task = Task(
            id=task_id,
            scenario="eval",
            input=case.input,
            status="pending",
            eval_run_id=run_id,
            eval_case_id=case_id,
            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        task_repo = TaskRepository(db)
        await task_repo.create(task)
        await repo.commit()
        logger.info("eval_case_task_created", run_id=run_id, case_id=case_id, task_id=task_id)

    # 修复-BUG-007 使用实际Agent编排执行用例输入
    model_output = ""
    task_error = None
    try:
        # 更新任务状态为running
        async with async_session_factory() as db:
            task_repo = TaskRepository(db)
            t = await task_repo.get_by_id(task_id)
            if t:
                t.status = "running"
                t.started_at = datetime.now(timezone.utc).replace(tzinfo=None)
                await task_repo.commit()

        # 执行Agent编排
        orchestrator = AgentOrchestrator(use_mcp=True, checkpoint_enabled=True)
        final_state = await orchestrator.run(
            user_input=case.input,
            task_id=task_id,
            trace_id=f"eval-{run_id}",
            max_rounds=3,  # 评测用例限制轮次
        )
        model_output = final_state.get("final_output", "")
        task_status = final_state.get("status", "completed")

        # 更新任务状态为完成
        async with async_session_factory() as db:
            task_repo = TaskRepository(db)
            t = await task_repo.get_by_id(task_id)
            if t:
                t.status = task_status
                t.result = model_output[:5000]
                t.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                if t.started_at:
                    t.duration_seconds = (t.completed_at - t.started_at).total_seconds()
                await task_repo.commit()

    except Exception as e:
        task_error = str(e)
        model_output = f"[Agent执行失败] {e}"
        logger.error("eval_case_agent_failed", run_id=run_id, case_id=case_id, task_id=task_id, error=str(e))
        # 更新任务状态为失败
        async with async_session_factory() as db:
            task_repo = TaskRepository(db)
            t = await task_repo.get_by_id(task_id)
            if t:
                t.status = "failed"
                t.error_message = str(e)[:1000]
                t.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                if t.started_at:
                    t.duration_seconds = (t.completed_at - t.started_at).total_seconds()
                await task_repo.commit()

    # 使用 Judge 评分
    judge = EvalJudge()
    judge_result = await judge.evaluate(
        case_input=case.input,
        model_output=model_output,  # 修复-BUG-007 使用实际Agent输出
        scoring_criteria=case.scoring_criteria,
        run_id=run_id,
    )

    # 计算最终评分
    scorer = EvalScorer()
    final_score = scorer.calculate_final_score(judge_result)

    # 保存评分记录
    async with async_session_factory() as db:
        repo = EvalRepository(db)
        score = EvalScore(
            run_id=run_id,
            case_id=case_id,
            task_id=task_id,  # 修复-BUG-008 关联Agent任务ID
            accuracy_score=judge_result.get("accuracy", 0),
            relevance_score=judge_result.get("relevance", 0),
            completeness_score=judge_result.get("completeness", 0),
            format_score=judge_result.get("format", 0),
            overall_score=final_score,
            judge_feedback=judge_result.get("feedback", ""),
        )
        await repo.insert_score(score)

        # 原子递增 completed_cases
        passed = final_score >= 6.0
        # 修复联调问题-REG-001 改动原因：Agent执行异常时标记 failed=True，
        # 使 failed_cases 字段正确递增，避免失败用例统计永远为 0
        case_failed = task_error is not None
        await repo.increment_completed_cases(run_id, passed=passed, failed=case_failed)
        await repo.commit()

    # 修复-BUG-009 异步模式下完成后触发check_completion
    try:
        from app.eval.eval_service import EvalService
        eval_service = EvalService()
        await eval_service.check_completion(run_id)
    except Exception as e:
        logger.error("eval_check_completion_failed", run_id=run_id, case_id=case_id, error=str(e))

    return {
        "run_id": run_id,
        "case_id": case_id,
        "task_id": task_id,
        "status": "completed",
        "overall_score": final_score,
        "passed": passed,
    }


# Celery 任务定义
if is_celery_enabled():

    @celery_app.task(bind=True, name="eval.run_case", max_retries=1)
    def run_eval_case_task(self, run_id: str, case_id: str) -> dict[str, Any]:
        """Celery 任务：执行单条评测用例。"""
        return _run_eval_case_sync(run_id, case_id)

else:

    def run_eval_case_task(run_id: str, case_id: str) -> dict[str, Any]:
        """同步执行评测用例（降级模式）。"""
        return _run_eval_case_sync(run_id, case_id)
