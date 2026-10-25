"""评测相关表 ORM 模型（T1-3）。

定义四张表：
- eval_cases：评测用例表（case_id 主键，INSERT OR IGNORE 幂等导入，P2-8）
- eval_runs：评测运行表（含 completed_cases 原子递增字段，P0-1）
- eval_scores：评测评分表（4 维度分数 + overall 加权平均）
- eval_reports：评测报告表

字段类型/约束/索引与架构 §4.2.5~4.2.8 完全一致。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class EvalCase(Base):
    """评测用例表（架构 §4.2.5）。

    存储评测用例库，case_id 为主键，支持 INSERT OR IGNORE 幂等导入（P2-8）。
    """

    __tablename__ = "eval_cases"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, comment="用例 ID（如 case-001）"
    )
    category: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="用例分类（research/code/general）"
    )
    input: Mapped[str] = mapped_column(Text, nullable=False, comment="用例输入")
    expected_output: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="期望输出（参考，非严格匹配）"
    )
    scoring_criteria: Mapped[str] = mapped_column(
        Text, nullable=False, comment="评分标准（注入 Judge 提示词）"
    )
    difficulty: Mapped[int] = mapped_column(Integer, nullable=False, default=1, comment="难度 1-5")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, comment="是否启用")

    __table_args__ = (
        Index("idx_eval_cases_category", "category", "enabled"),
    )


class EvalRun(Base):
    """评测运行表（架构 §4.2.6）。

    记录一次评测运行的状态和进度，completed_cases 字段用于原子递增（P0-1）。
    """

    __tablename__ = "eval_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, comment="运行 ID")
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="运行名称")
    model_config: Mapped[str] = mapped_column(Text, nullable=False, comment="模型配置快照（JSON）")
    status: Mapped[str] = mapped_column(
        # 修复Bug-005 改动原因：default和comment统一为小写，与eval_service.py写入的running/completed及Task模型保持一致
        String(20), nullable=False, default="pending", comment="pending/running/completed/failed"
    )
    total_cases: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="总用例数")
    # P0-1：原子递增字段，单条 UPDATE 操作保证并发安全
    completed_cases: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="已完成用例数（原子递增）"
    )
    passed_cases: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="通过用例数（overall ≥ 6）"
    )
    failed_cases: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="失败用例数（修复-BUG-011 架构复审建议1，区分失败用例）"
    )
    avg_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="平均总分")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), comment="创建时间"
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, comment="完成时间")

    # 关联关系
    scores: Mapped[list["EvalScore"]] = relationship(
        back_populates="eval_run", cascade="all, delete-orphan"
    )
    report: Mapped[Optional["EvalReport"]] = relationship(
        back_populates="eval_run", cascade="all, delete-orphan", uselist=False
    )

    __table_args__ = (
        Index("idx_eval_runs_status", "status"),
    )


class EvalScore(Base):
    """评测评分表（架构 §4.2.7）。

    记录每条用例的 4 维度评分和 overall 加权平均（T6-4）。
    overall = accuracy*0.3 + relevance*0.3 + completeness*0.25 + format*0.15
    """

    __tablename__ = "eval_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("eval_runs.id", ondelete="CASCADE"), nullable=False
    )
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("eval_cases.id", ondelete="RESTRICT"), nullable=False
    )
    task_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, comment="关联的 Agent 任务"
    )
    accuracy_score: Mapped[float] = mapped_column(Float, nullable=False, comment="准确性 0-10")
    relevance_score: Mapped[float] = mapped_column(Float, nullable=False, comment="相关性 0-10")
    completeness_score: Mapped[float] = mapped_column(Float, nullable=False, comment="完整性 0-10")
    format_score: Mapped[float] = mapped_column(Float, nullable=False, comment="格式合规性 0-10")
    overall_score: Mapped[float] = mapped_column(
        Float, nullable=False, comment="总分（加权平均）"
    )
    judge_feedback: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="Judge 文字反馈")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), comment="评分时间"
    )

    eval_run: Mapped["EvalRun"] = relationship(back_populates="scores")

    __table_args__ = (
        Index("idx_scores_run", "run_id"),
        Index("idx_scores_case", "case_id"),
    )


class EvalReport(Base):
    """评测报告表（架构 §4.2.8）。

    记录生成的评测报告文件元数据，run_id 唯一约束（1:1 关系）。
    """

    __tablename__ = "eval_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("eval_runs.id", ondelete="CASCADE"), nullable=False
    )
    report_type: Mapped[str] = mapped_column(String(20), nullable=False, comment="html/json")
    file_path: Mapped[str] = mapped_column(String(512), nullable=False, comment="本地文件路径")
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, comment="文件大小")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), comment="生成时间"
    )

    eval_run: Mapped["EvalRun"] = relationship(back_populates="report")

    __table_args__ = (
        UniqueConstraint("run_id", "report_type", name="uq_report_run_type"),
    )
