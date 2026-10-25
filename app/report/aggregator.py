"""报告聚合统计（T7-1）。

从评分记录聚合统计指标：
- 总体统计：平均分、通过率、中位数、标准差
- 按类别分组统计
- 按难度分组统计
- 维度平均分（accuracy/relevance/completeness/format）
- 用例排名（最高分/最低分）

架构决策（T-01）：聚合统计为纯计算逻辑，
输入评分记录列表，输出结构化统计字典，
便于 HTML 和 JSON 报告复用。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.eval.scorer import EvalScorer
from app.models.eval import EvalScore


class ReportAggregator:
    """报告聚合统计器（T7-1）。

    从评分记录计算各类统计指标。
    """

    def __init__(self) -> None:
        """初始化聚合器。"""
        self.scorer = EvalScorer()

    def aggregate(
        self,
        scores: list[EvalScore],
        run_info: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """执行完整聚合统计。

        Args:
            scores: 评分记录列表
            run_info: 运行信息（名称、模型配置等）

        Returns:
            完整统计字典
        """
        overall_scores = [s.overall_score for s in scores if s.overall_score is not None]

        return {
            "run_info": run_info or {},
            "summary": self._summary_stats(overall_scores),
            "dimension_stats": self._dimension_stats(scores),
            "by_category": self._by_category(scores),
            "by_difficulty": self._by_difficulty(scores),
            "top_cases": self._top_cases(scores, n=5),
            "bottom_cases": self._bottom_cases(scores, n=5),
            "score_distribution": self._score_distribution(overall_scores),
            "total_scores": len(scores),
        }

    def _summary_stats(self, scores: list[float]) -> dict[str, Any]:
        """计算总体统计指标。

        Args:
            scores: 综合分数列表

        Returns:
            统计字典
        """
        return self.scorer.calculate_stats(scores)

    @staticmethod
    def _dimension_stats(scores: list[EvalScore]) -> dict[str, float]:
        """计算各维度平均分。

        Args:
            scores: 评分记录列表

        Returns:
            各维度平均分之典
        """
        if not scores:
            return {"accuracy": 0, "relevance": 0, "completeness": 0, "format": 0}

        dimensions = ["accuracy_score", "relevance_score", "completeness_score", "format_score"]
        result = {}
        for dim in dimensions:
            values = [getattr(s, dim) for s in scores if getattr(s, dim) is not None]
            avg = sum(values) / len(values) if values else 0
            result[dim.replace("_score", "")] = round(avg, 2)
        return result

    def _by_category(self, scores: list[EvalScore]) -> dict[str, dict[str, Any]]:
        """按类别分组统计。

        Args:
            scores: 评分记录列表

        Returns:
            按类别分组的统计
        """
        # EvalScore 没有 category 字段，需要通过 case_id 关联
        # 简化：按 case_id 前缀分组（case-research-001 -> research）
        groups: dict[str, list[float]] = defaultdict(list)
        for s in scores:
            if s.overall_score is None:
                continue
            # 从 case_id 提取类别
            parts = s.case_id.split("-")
            category = parts[1] if len(parts) >= 2 else "unknown"
            groups[category].append(s.overall_score)

        return {cat: self.scorer.calculate_stats(s) for cat, s in groups.items()}

    def _by_difficulty(self, scores: list[EvalScore]) -> dict[str, dict[str, Any]]:
        """按难度分组统计（简化：从 case_id 无法直接获取难度，返回空）。

        Args:
            scores: 评分记录列表

        Returns:
            按难度分组的统计
        """
        # 难度信息在 EvalCase 表中，需要关联查询
        # 此处简化返回空，实际使用时应传入 case 信息
        return {}

    @staticmethod
    def _top_cases(scores: list[EvalScore], n: int = 5) -> list[dict[str, Any]]:
        """获取最高分用例。

        Args:
            scores: 评分记录列表
            n: 返回数量

        Returns:
            最高分用例列表
        """
        valid = [s for s in scores if s.overall_score is not None]
        valid.sort(key=lambda x: x.overall_score, reverse=True)
        return [
            {"case_id": s.case_id, "score": s.overall_score, "feedback": s.judge_feedback[:100]}
            for s in valid[:n]
        ]

    @staticmethod
    def _bottom_cases(scores: list[EvalScore], n: int = 5) -> list[dict[str, Any]]:
        """获取最低分用例。

        Args:
            scores: 评分记录列表
            n: 返回数量

        Returns:
            最低分用例列表
        """
        valid = [s for s in scores if s.overall_score is not None]
        valid.sort(key=lambda x: x.overall_score)
        return [
            {"case_id": s.case_id, "score": s.overall_score, "feedback": s.judge_feedback[:100]}
            for s in valid[:n]
        ]

    @staticmethod
    def _score_distribution(scores: list[float]) -> dict[str, int]:
        """计算分数分布（0-2, 2-4, 4-6, 6-8, 8-10）。

        Args:
            scores: 综合分数列表

        Returns:
            分数分布字典
        """
        distribution = {"0-2": 0, "2-4": 0, "4-6": 0, "6-8": 0, "8-10": 0}
        for s in scores:
            if s < 2:
                distribution["0-2"] += 1
            elif s < 4:
                distribution["2-4"] += 1
            elif s < 6:
                distribution["4-6"] += 1
            elif s < 8:
                distribution["6-8"] += 1
            else:
                distribution["8-10"] += 1
        return distribution
