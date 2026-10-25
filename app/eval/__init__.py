"""评测框架（M6）。

提供 LLM 评测能力：
- EvalCaseLoader: YAML 用例加载器
- EvalJudge: LLM 评分器
- EvalScorer: 评分计算
- EvalService: 评测运行管理
"""

from app.eval.eval_service import EvalService
from app.eval.judge import EvalJudge
from app.eval.loader import EvalCaseLoader
from app.eval.scorer import EvalScorer

__all__ = ["EvalService", "EvalJudge", "EvalCaseLoader", "EvalScorer"]
