"""评测数据访问层（T1-5）。

封装 eval_cases / eval_runs / eval_scores / eval_reports 四张表的 CRUD 操作。

核心操作：
- 用例幂等导入（INSERT OR IGNORE，P2-8）
- 评测运行创建/查询/状态更新
- completed_cases 原子递增（P0-1，单条 UPDATE）
- 评分记录插入
- 报告记录插入/查询
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.eval import EvalCase, EvalReport, EvalRun, EvalScore

logger = get_logger(__name__)


class EvalRepository:
    """评测数据访问对象。"""

    def __init__(self, db: AsyncSession) -> None:
        """初始化 Repository。

        Args:
            db: 异步数据库 Session
        """
        self.db = db

    # === 用例相关 ===

    async def upsert_case(self, case: EvalCase) -> bool:
        """幂等导入评测用例（INSERT OR IGNORE，P2-8）。

        基于 case_id 主键，重复执行不产生重复数据。

        Args:
            case: 用例对象

        Returns:
            True 表示新插入，False 表示已存在被跳过
        """
        stmt = sqlite_insert(EvalCase).values(
            id=case.id,
            category=case.category,
            input=case.input,
            expected_output=case.expected_output,
            scoring_criteria=case.scoring_criteria,
            difficulty=case.difficulty,
            enabled=case.enabled,
        )
        stmt = stmt.on_conflict_do_nothing(index_elements=["id"])
        result = await self.db.execute(stmt)
        inserted = result.rowcount > 0
        if inserted:
            logger.debug("eval_case_inserted", case_id=case.id)
        return inserted

    async def get_cases(
        self,
        category: Optional[str] = None,
        enabled: Optional[bool] = None,
    ) -> list[EvalCase]:
        """查询评测用例列表。

        Args:
            category: 按分类筛选（可选）
            enabled: 按启用状态筛选（可选）

        Returns:
            用例列表
        """
        query = select(EvalCase)
        if category:
            query = query.where(EvalCase.category == category)
        if enabled is not None:
            query = query.where(EvalCase.enabled == enabled)
        query = query.order_by(EvalCase.id)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_case_by_id(self, case_id: str) -> Optional[EvalCase]:
        """根据 ID 查询用例。

        Args:
            case_id: 用例 ID

        Returns:
            用例对象，不存在时返回 None
        """
        result = await self.db.execute(select(EvalCase).where(EvalCase.id == case_id))
        return result.scalar_one_or_none()

    # === 评测运行相关 ===

    async def create_run(self, run: EvalRun) -> EvalRun:
        """创建评测运行。

        Args:
            run: 运行对象

        Returns:
            创建后的对象（已刷新）
        """
        self.db.add(run)
        await self.db.flush()
        await self.db.refresh(run)
        logger.info("eval_run_created", run_id=run.id, total_cases=run.total_cases)
        return run

    async def get_run_by_id(self, run_id: str) -> Optional[EvalRun]:
        """根据 ID 查询评测运行。

        Args:
            run_id: 运行 ID

        Returns:
            运行对象，不存在时返回 None
        """
        result = await self.db.execute(select(EvalRun).where(EvalRun.id == run_id))
        return result.scalar_one_or_none()

    async def update_run_status(self, run_id: str, status: str) -> Optional[EvalRun]:
        """更新评测运行状态。

        Args:
            run_id: 运行 ID
            status: 新状态

        Returns:
            更新后的对象，不存在时返回 None
        """
        run = await self.get_run_by_id(run_id)
        if not run:
            return None
        run.status = status
        # 修复Bug-006 改动原因：状态判断统一为小写，与eval_service写入的completed及task_repo修复保持一致
        if status == "completed":
            run.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await self.db.flush()
        logger.info("eval_run_status_updated", run_id=run_id, status=status)
        return run

    async def increment_completed_cases(
        self, run_id: str, passed: bool = False, failed: bool = False
    ) -> Optional[EvalRun]:
        """completed_cases 原子递增（P0-1）。

        使用单条 UPDATE 语句保证并发安全，避免先读后写的竞态条件。
        同时根据 passed 参数递增 passed_cases，根据 failed 参数递增 failed_cases。

        修复联调问题-REG-001 改动原因：BUG-011 添加 failed_cases 字段后未递增，
        导致失败用例统计永远为 0，用例完全失败时运行可能永久停留 running。

        Args:
            run_id: 运行 ID
            passed: 该用例是否通过（overall ≥ 6）
            failed: 该用例是否执行失败（Agent 执行异常）

        Returns:
            更新后的运行对象，不存在时返回 None
        """
        # 原子递增：单条 UPDATE，SQLite 保证语句级原子性
        set_clause = {EvalRun.completed_cases: EvalRun.completed_cases + 1}
        if passed:
            set_clause[EvalRun.passed_cases] = EvalRun.passed_cases + 1
        # 修复联调问题-REG-001 改动原因：递增 failed_cases 字段
        if failed:
            set_clause[EvalRun.failed_cases] = EvalRun.failed_cases + 1

        stmt = update(EvalRun).where(EvalRun.id == run_id).values(set_clause)
        result = await self.db.execute(stmt)
        if result.rowcount == 0:
            return None

        await self.db.flush()
        run = await self.get_run_by_id(run_id)
        logger.debug(
            "eval_run_completed_incremented",
            run_id=run_id,
            completed=run.completed_cases if run else None,
            passed=run.passed_cases if run else None,
            failed=run.failed_cases if run else None,
        )
        return run

    async def update_run_stats(self, run_id: str, avg_score: float) -> None:
        """更新评测运行的统计数据。

        Args:
            run_id: 运行 ID
            avg_score: 平均总分
        """
        run = await self.get_run_by_id(run_id)
        if run:
            run.avg_score = avg_score
            await self.db.flush()

    async def list_runs(
        self,
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[EvalRun], int]:
        """分页查询评测运行列表。

        Args:
            status: 按状态筛选（可选）
            page: 页码
            page_size: 每页数量

        Returns:
            (运行列表, 总数) 元组
        """
        page_size = min(page_size, 100)
        offset = (page - 1) * page_size

        query = select(EvalRun)
        if status:
            query = query.where(EvalRun.status == status)

        count_query = select(func.count()).select_from(query.subquery())
        total = (await self.db.execute(count_query)).scalar_one()

        query = query.order_by(EvalRun.created_at.desc()).offset(offset).limit(page_size)
        result = await self.db.execute(query)
        runs = list(result.scalars().all())

        return runs, total

    # === 评分相关 ===

    async def insert_score(self, score: EvalScore) -> EvalScore:
        """插入评分记录。

        Args:
            score: 评分对象

        Returns:
            插入后的对象（已刷新）
        """
        self.db.add(score)
        await self.db.flush()
        await self.db.refresh(score)
        return score

    async def get_scores_by_run(self, run_id: str) -> list[EvalScore]:
        """查询运行的全部评分记录。

        Args:
            run_id: 运行 ID

        Returns:
            评分记录列表
        """
        query = select(EvalScore).where(EvalScore.run_id == run_id).order_by(EvalScore.case_id)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    # === 报告相关 ===

    async def insert_report(self, report: EvalReport) -> EvalReport:
        """插入报告记录。

        Args:
            report: 报告对象

        Returns:
            插入后的对象（已刷新）
        """
        self.db.add(report)
        await self.db.flush()
        await self.db.refresh(report)
        return report

    async def get_report(self, run_id: str, report_type: str) -> Optional[EvalReport]:
        """查询报告记录。

        Args:
            run_id: 运行 ID
            report_type: 报告类型（html/json）

        Returns:
            报告对象，不存在时返回 None
        """
        query = select(EvalReport).where(
            EvalReport.run_id == run_id, EvalReport.report_type == report_type
        )
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def commit(self) -> None:
        """提交事务。"""
        await self.db.commit()

    async def rollback(self) -> None:
        """回滚事务。"""
        await self.db.rollback()
