"""评测运行服务（T6-5/T6-6/T6-7）。

管理评测运行的完整生命周期：
- 创建运行（T6-5）：初始化 EvalRun，关联用例集
- 执行用例（T6-5）：逐条或批量执行评测用例
- 完成检测（T6-6）：completed_cases == total_cases 时标记完成
- 一致性校验（T6-7）：校验评分记录与运行统计一致
- 运行查询：状态/结果/统计

架构决策（T-01）：评测运行为异步执行，
通过 Celery 任务逐条执行用例，原子递增计数器，
完成后自动生成报告。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.exceptions import AppException, ErrorCode
from app.core.logging import get_logger
from app.eval.judge import EvalJudge
from app.eval.scorer import EvalScorer
from app.models.base import async_session_factory
from app.models.eval import EvalCase, EvalReport, EvalRun, EvalScore
from app.repositories.eval_repo import EvalRepository
from app.workers.celery_app import is_celery_enabled
from app.workers.tasks.eval_case import run_eval_case_task

logger = get_logger(__name__)


class EvalService:
    """评测运行服务（T6-5）。

    管理评测运行的创建、执行、查询和完成检测。
    """

    def __init__(self) -> None:
        """初始化评测服务。"""
        self.judge = EvalJudge()
        self.scorer = EvalScorer()

    async def create_run(
        self,
        name: str,
        model_config: dict[str, Any],
        case_ids: Optional[list[str]] = None,
        category: Optional[str] = None,
    ) -> EvalRun:
        """创建评测运行（T6-5）。

        Args:
            name: 运行名称
            model_config: 模型配置（模型名称、参数等）
            case_ids: 指定用例 ID 列表，为 None 时使用全部启用用例
            category: 按类别筛选用例（与 case_ids 互斥）

        Returns:
            创建的 EvalRun 对象
        """
        run_id = str(uuid.uuid4())

        async with async_session_factory() as db:
            repo = EvalRepository(db)

            # 获取用例列表
            if case_ids:
                cases = []
                for cid in case_ids:
                    case = await repo.get_case_by_id(cid)
                    if case:
                        cases.append(case)
            else:
                cases = await repo.get_cases(category=category, enabled=True)

            if not cases:
                raise AppException(ErrorCode.VALIDATION_ERROR, "没有可用的评测用例")

            # 创建运行记录
            # 修复：用 json.dumps 序列化为合法 JSON 字符串（而非 str(dict) 产生的 Python 单引号字符串）
            import json as _json
            run = EvalRun(
                id=run_id,
                name=name,
                model_config=_json.dumps(model_config, ensure_ascii=False),
                total_cases=len(cases),
                completed_cases=0,
                passed_cases=0,
                status="running",
                created_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
            await repo.create_run(run)
            await repo.commit()

        logger.info("eval_run_created", run_id=run_id, name=name, total_cases=len(cases))

        # 提交用例执行任务
        for case in cases:
            if is_celery_enabled():
                run_eval_case_task.delay(run_id=run_id, case_id=case.id)
            else:
                # 同步降级：直接执行（开发环境）
                import asyncio
                asyncio.ensure_future(self._execute_case(run_id, case.id))

        return run

    async def _execute_case(self, run_id: str, case_id: str) -> None:
        """异步执行单条用例（同步降级模式）。

        修复Bug-REG2-002 改动原因：同步降级模式下直接调用run_eval_case_task()，
        其内部_run_eval_case_sync使用asyncio.run()创建新事件循环，
        与FastAPI当前事件循环冲突导致RuntimeError。
        改用run_in_executor将同步函数放到独立线程执行，线程内无预存事件循环。

        Args:
            run_id: 运行 ID
            case_id: 用例 ID
        """
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            # 修复Bug-REG2-002 改动原因：在独立线程中运行同步函数，避免asyncio.run与当前事件循环冲突
            await loop.run_in_executor(None, lambda: run_eval_case_task(run_id=run_id, case_id=case_id))
            # 检查是否完成
            await self.check_completion(run_id)
        except Exception as e:
            logger.error("eval_case_execution_failed", run_id=run_id, case_id=case_id, error=str(e))

    async def get_run(self, run_id: str) -> EvalRun:
        """获取评测运行详情。

        Args:
            run_id: 运行 ID

        Returns:
            EvalRun 对象

        Raises:
            AppException(NOT_FOUND): 运行不存在时抛出
        """
        async with async_session_factory() as db:
            repo = EvalRepository(db)
            run = await repo.get_run_by_id(run_id)
            if not run:
                raise AppException(ErrorCode.NOT_FOUND, f"评测运行 {run_id} 不存在")
            return run

    async def list_runs(
        self,
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[EvalRun], int]:
        """分页查询评测运行列表。

        Args:
            status: 按状态筛选
            page: 页码
            page_size: 每页数量

        Returns:
            (运行列表, 总数)
        """
        async with async_session_factory() as db:
            repo = EvalRepository(db)
            return await repo.list_runs(status=status, page=page, page_size=page_size)

    async def check_completion(self, run_id: str) -> Optional[EvalRun]:
        """完成检测（T6-6）。

        当 completed_cases == total_cases 时，标记运行完成，
        计算平均分，生成报告。

        修复-BUG-012: 添加原子条件更新，仅status=running时才完成，防止高并发竞态导致重复生成报告

        Args:
            run_id: 运行 ID

        Returns:
            更新后的 EvalRun，未完成时返回 None
        """
        from sqlalchemy import update

        async with async_session_factory() as db:
            repo = EvalRepository(db)
            run = await repo.get_run_by_id(run_id)
            if not run:
                return None

            # 检查是否完成
            if run.completed_cases < run.total_cases:
                return None

            # 修复-BUG-012 原子条件更新：仅当status='running'时才更新为completed
            # 使用UPDATE ... WHERE status='running'，通过rowcount判断是否成功获取锁
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            stmt = (
                update(EvalRun)
                .where(EvalRun.id == run_id, EvalRun.status == "running")
                .values(status="completed", completed_at=now)
            )
            result = await db.execute(stmt)
            if result.rowcount == 0:
                # 另一个并发请求已经完成了该运行，直接返回
                logger.info("eval_run_already_completed", run_id=run_id)
                return await repo.get_run_by_id(run_id)

            await db.flush()

            # 已完成，计算统计
            scores = await repo.get_scores_by_run(run_id)
            overall_scores = [s.overall_score for s in scores if s.overall_score is not None]

            if overall_scores:
                avg_score = sum(overall_scores) / len(overall_scores)
            else:
                avg_score = 0.0

            # 更新运行统计
            run.status = "completed"
            run.avg_score = round(avg_score, 2)
            run.completed_at = now
            await repo.update_run_stats(run_id, avg_score)
            await repo.commit()

            # 生成报告
            await self._generate_report(run, scores)

            logger.info(
                "eval_run_completed",
                run_id=run_id,
                total=run.total_cases,
                passed=run.passed_cases,
                avg_score=avg_score,
            )
            return run

    async def _generate_report(self, run: EvalRun, scores: list[EvalScore]) -> None:
        """生成评测报告（T6-5，调用 M7）。

        Args:
            run: 评测运行
            scores: 评分记录列表
        """
        try:
            from app.report.generator import ReportGenerator

            generator = ReportGenerator()
            await generator.generate_report(run, scores)
        except ImportError:
            logger.warning("report_generator_not_available, skipping report generation")
        except Exception as e:
            logger.error("report_generation_failed", run_id=run.id, error=str(e))

    async def get_run_scores(self, run_id: str) -> list[EvalScore]:
        """获取运行的全部评分记录。

        Args:
            run_id: 运行 ID

        Returns:
            评分记录列表
        """
        async with async_session_factory() as db:
            repo = EvalRepository(db)
            return await repo.get_scores_by_run(run_id)

    async def validate_consistency(self, run_id: str) -> dict[str, Any]:
        """一致性校验（T6-7）。

        校验评分记录数量与运行统计是否一致：
        - 评分记录数 == completed_cases
        - passed_cases == 评分中 overall ≥ 6 的数量

        Args:
            run_id: 运行 ID

        Returns:
            校验结果字典，含 passed/errors/details
        """
        async with async_session_factory() as db:
            repo = EvalRepository(db)
            run = await repo.get_run_by_id(run_id)
            if not run:
                return {"passed": False, "errors": ["运行不存在"], "details": {}}

            scores = await repo.get_scores_by_run(run_id)
            score_count = len(scores)
            passed_count = sum(1 for s in scores if s.overall_score and s.overall_score >= 6.0)

            errors: list[str] = []
            if score_count != run.completed_cases:
                errors.append(
                    f"评分记录数({score_count}) != completed_cases({run.completed_cases})"
                )
            if passed_count != run.passed_cases:
                errors.append(
                    f"通过数({passed_count}) != passed_cases({run.passed_cases})"
                )

            return {
                "passed": len(errors) == 0,
                "errors": errors,
                "details": {
                    "score_count": score_count,
                    "completed_cases": run.completed_cases,
                    "passed_count": passed_count,
                    "passed_cases": run.passed_cases,
                    "total_cases": run.total_cases,
                },
            }
