"""评分计算（T6-4）。

计算最终评分和统计指标：
- 加权综合分（默认等权重）
- 通过率（overall ≥ 6 为通过）
- 按类别/难度分组统计
- 平均分、中位数、标准差

架构决策（T-01）：评分计算为纯函数，
不依赖外部服务，便于单元测试和复用。
"""

from __future__ import annotations

import math
from typing import Any, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)

# 默认维度权重（等权重）
DEFAULT_WEIGHTS = {
    "accuracy": 0.30,
    "relevance": 0.25,
    "completeness": 0.25,
    "format": 0.20,
}

# 通过阈值
PASS_THRESHOLD = 6.0


class EvalScorer:
    """评分计算器（T6-4）。

    提供评分计算和统计方法。
    """

    def __init__(self, weights: Optional[dict[str, float]] = None) -> None:
        """初始化评分器。

        Args:
            weights: 维度权重，为 None 时使用默认等权重
        """
        self.weights = weights or DEFAULT_WEIGHTS

    def calculate_final_score(self, judge_result: dict[str, Any]) -> float:
        """计算加权综合分。

        Args:
            judge_result: Judge 评分结果，含 accuracy/relevance/completeness/format

        Returns:
            加权综合分（0-10），保留 2 位小数
        """
        total_weight = sum(self.weights.values())
        weighted_sum = 0.0

        for dimension, weight in self.weights.items():
            score = float(judge_result.get(dimension, 0))
            weighted_sum += score * weight

        final_score = weighted_sum / total_weight if total_weight > 0 else 0
        return round(final_score, 2)

    @staticmethod
    def is_passed(overall_score: float, threshold: float = PASS_THRESHOLD) -> bool:
        """判断是否通过。

        Args:
            overall_score: 综合分
            threshold: 通过阈值

        Returns:
            True 表示通过
        """
        return overall_score >= threshold

    def calculate_stats(self, scores: list[float]) -> dict[str, Any]:
        """计算评分统计指标。

        Args:
            scores: 评分数组

        Returns:
            统计字典，含 count/mean/median/std/min/max/pass_rate
        """
        if not scores:
            return {
                "count": 0,
                "mean": 0,
                "median": 0,
                "std": 0,
                "min": 0,
                "max": 0,
                "pass_rate": 0,
            }

        n = len(scores)
        sorted_scores = sorted(scores)
        mean = sum(scores) / n

        # 中位数
        if n % 2 == 0:
            median = (sorted_scores[n // 2 - 1] + sorted_scores[n // 2]) / 2
        else:
            median = sorted_scores[n // 2]

        # 标准差
        variance = sum((x - mean) ** 2 for x in scores) / n
        std = math.sqrt(variance)

        # 通过率
        passed = sum(1 for s in scores if self.is_passed(s))
        pass_rate = passed / n

        return {
            "count": n,
            "mean": round(mean, 2),
            "median": round(median, 2),
            "std": round(std, 2),
            "min": min(scores),
            "max": max(scores),
            "pass_rate": round(pass_rate, 4),
            "passed_count": passed,
            "failed_count": n - passed,
        }

    def group_by_category(
        self, results: list[dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        """按类别分组统计。

        Args:
            results: 评测结果列表，每项含 category 和 overall_score

        Returns:
            按类别分组的统计字典
        """
        groups: dict[str, list[float]] = {}
        for r in results:
            category = r.get("category", "unknown")
            score = float(r.get("overall_score", 0))
            groups.setdefault(category, []).append(score)

        return {
            category: self.calculate_stats(scores)
            for category, scores in groups.items()
        }

    def group_by_difficulty(
        self, results: list[dict[str, Any]]
    ) -> dict[int, dict[str, Any]]:
        """按难度分组统计。

        Args:
            results: 评测结果列表，每项含 difficulty 和 overall_score

        Returns:
            按难度分组的统计字典
        """
        groups: dict[int, list[float]] = {}
        for r in results:
            difficulty = int(r.get("difficulty", 1))
            score = float(r.get("overall_score", 0))
            groups.setdefault(difficulty, []).append(score)

        return {
            difficulty: self.calculate_stats(scores)
            for difficulty, scores in sorted(groups.items())
        }
