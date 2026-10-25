"""评测框架单元测试（M12）。

覆盖：用例加载器、Judge 评分器、评分计算、一致性校验。
"""

from __future__ import annotations

import pytest

from app.eval.scorer import EvalScorer


class TestEvalScorer:
    """评分计算器测试（T6-4）。"""

    def test_calculate_final_score(self) -> None:
        """测试加权综合分计算。"""
        scorer = EvalScorer()
        result = scorer.calculate_final_score({
            "accuracy": 8.0,
            "relevance": 7.0,
            "completeness": 6.0,
            "format": 9.0,
        })
        # 加权：8*0.3 + 7*0.25 + 6*0.25 + 9*0.2 = 2.4 + 1.75 + 1.5 + 1.8 = 7.45
        assert result == 7.45

    def test_is_passed(self) -> None:
        """测试通过判定。"""
        assert EvalScorer.is_passed(6.0) is True
        assert EvalScorer.is_passed(5.9) is False
        assert EvalScorer.is_passed(10.0) is True

    def test_calculate_stats_empty(self) -> None:
        """测试空数组统计。"""
        scorer = EvalScorer()
        stats = scorer.calculate_stats([])
        assert stats["count"] == 0
        assert stats["mean"] == 0
        assert stats["pass_rate"] == 0

    def test_calculate_stats_normal(self) -> None:
        """测试正常统计计算。"""
        scorer = EvalScorer()
        stats = scorer.calculate_stats([8.0, 6.0, 4.0, 9.0, 7.0])
        assert stats["count"] == 5
        assert stats["mean"] == 6.8
        assert stats["median"] == 7.0
        assert stats["min"] == 4.0
        assert stats["max"] == 9.0
        assert stats["passed_count"] == 4  # 8,6,9,7 >= 6
        assert stats["failed_count"] == 1

    def test_custom_weights(self) -> None:
        """测试自定义权重。"""
        scorer = EvalScorer(weights={"accuracy": 0.5, "relevance": 0.5})
        result = scorer.calculate_final_score({"accuracy": 10.0, "relevance": 0.0})
        assert result == 5.0


class TestEvalCaseLoader:
    """用例加载器测试（T6-2）。"""

    def test_validate_cases_valid(self) -> None:
        """测试合法用例校验通过。"""
        from app.eval.loader import EvalCaseLoader

        cases = [
            {"id": "case-001", "input": "test", "scoring_criteria": "acc", "difficulty": 2, "category": "research"},
        ]
        errors = EvalCaseLoader.validate_cases(cases)
        assert len(errors) == 0

    def test_validate_cases_missing_fields(self) -> None:
        """测试缺少必填字段的用例校验失败。"""
        from app.eval.loader import EvalCaseLoader

        cases = [{"id": "case-001"}]  # 缺少 input 和 scoring_criteria
        errors = EvalCaseLoader.validate_cases(cases)
        assert len(errors) >= 2

    def test_validate_cases_duplicate_id(self) -> None:
        """测试重复 ID 校验失败。"""
        from app.eval.loader import EvalCaseLoader

        cases = [
            {"id": "dup", "input": "a", "scoring_criteria": "b"},
            {"id": "dup", "input": "c", "scoring_criteria": "d"},
        ]
        errors = EvalCaseLoader.validate_cases(cases)
        assert any("重复" in e for e in errors)

    def test_validate_cases_invalid_difficulty(self) -> None:
        """测试无效难度校验失败。"""
        from app.eval.loader import EvalCaseLoader

        cases = [{"id": "c1", "input": "a", "scoring_criteria": "b", "difficulty": 10}]
        errors = EvalCaseLoader.validate_cases(cases)
        assert any("difficulty" in e for e in errors)


class TestEvalJudge:
    """Judge 评分器测试（T6-3）。"""

    @pytest.mark.asyncio
    async def test_judge_returns_scores(self) -> None:
        """测试 Judge 返回评分（Mock 模式）。"""
        from app.eval.judge import EvalJudge

        judge = EvalJudge()
        result = await judge.evaluate(
            case_input="测试问题",
            model_output="测试回答",
            scoring_criteria="准确性",
            run_id="test-run",
        )
        assert "accuracy" in result
        assert "relevance" in result
        assert "completeness" in result
        assert "format" in result
        assert "feedback" in result
        assert 0 <= result["accuracy"] <= 10
