"""评测用例加载器（T6-2）。

从 YAML 文件加载评测用例，支持：
- 单文件加载（cases.yaml）
- 按类别/难度筛选
- 幂等导入数据库（INSERT OR IGNORE）
- 用例数量校验（T-02 ≥30条）

架构决策（T-01）：用例以 YAML 文件管理，
便于版本控制和人工编辑，启动时加载到数据库。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml

from app.config import get_config, get_config_paths
from app.core.exceptions import AppException, ErrorCode
from app.core.logging import get_logger
from app.models.base import async_session_factory
from app.models.eval import EvalCase
from app.repositories.eval_repo import EvalRepository

logger = get_logger(__name__)

# 默认用例文件路径
DEFAULT_CASES_FILE = Path("data/eval_cases/cases.yaml")


class EvalCaseLoader:
    """评测用例加载器（T6-2）。

    从 YAML 文件加载用例并导入数据库。
    """

    def __init__(self, cases_file: Optional[Path] = None) -> None:
        """初始化加载器。

        Args:
            cases_file: 用例 YAML 文件路径，为 None 时使用默认路径
        """
        config = get_config()
        paths = get_config_paths()
        self.cases_file = cases_file or paths.get("eval_cases", DEFAULT_CASES_FILE)

    def load_from_yaml(self) -> list[dict[str, Any]]:
        """从 YAML 文件加载用例。

        Returns:
            用例字典列表

        Raises:
            AppException: 文件不存在或格式错误时抛出
        """
        if not self.cases_file.exists():
            raise AppException(
                ErrorCode.VALIDATION_ERROR,
                f"评测用例文件不存在: {self.cases_file}",
            )

        try:
            with open(self.cases_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise AppException(
                ErrorCode.VALIDATION_ERROR,
                f"评测用例 YAML 解析失败: {e}",
            ) from e

        cases = data.get("cases", [])
        logger.info("eval_cases_loaded_from_yaml", count=len(cases), file=str(self.cases_file))
        return cases

    async def import_to_db(self, cases: Optional[list[dict[str, Any]]] = None) -> tuple[int, int]:
        """将用例导入数据库（幂等，INSERT OR IGNORE）。

        Args:
            cases: 用例列表，为 None 时从 YAML 加载

        Returns:
            (新导入数量, 总数量)
        """
        if cases is None:
            cases = self.load_from_yaml()

        # 校验用例数量（T-02 ≥30条）
        if len(cases) < 30:
            logger.warning("eval_cases_below_minimum", count=len(cases), minimum=30)

        imported = 0
        async with async_session_factory() as db:
            repo = EvalRepository(db)
            for case_data in cases:
                case = EvalCase(
                    id=case_data["id"],
                    category=case_data.get("category", "general"),
                    input=case_data["input"],
                    scoring_criteria=case_data.get("scoring_criteria", ""),
                    difficulty=case_data.get("difficulty", 1),
                    enabled=case_data.get("enabled", True),
                )
                if await repo.upsert_case(case):
                    imported += 1
            await repo.commit()

        total = len(cases)
        logger.info("eval_cases_imported", imported=imported, total=total)
        return imported, total

    async def get_cases(
        self,
        category: Optional[str] = None,
        difficulty: Optional[int] = None,
        enabled_only: bool = True,
    ) -> list[EvalCase]:
        """从数据库查询用例。

        Args:
            category: 按类别筛选
            difficulty: 按难度筛选
            enabled_only: 仅返回启用的用例

        Returns:
            用例列表
        """
        async with async_session_factory() as db:
            repo = EvalRepository(db)
            return await repo.get_cases(category=category, enabled=True if enabled_only else None)

    @staticmethod
    def validate_cases(cases: list[dict[str, Any]]) -> list[str]:
        """校验用例格式（T6-7 一致性校验）。

        Args:
            cases: 用例列表

        Returns:
            错误信息列表，空列表表示全部通过
        """
        errors: list[str] = []
        seen_ids: set[str] = set()

        for i, case in enumerate(cases):
            prefix = f"用例[{i}]"

            # 必填字段
            if "id" not in case:
                errors.append(f"{prefix}: 缺少 id 字段")
            elif case["id"] in seen_ids:
                errors.append(f"{prefix}: id 重复 ({case['id']})")
            else:
                seen_ids.add(case["id"])

            if "input" not in case or not case["input"]:
                errors.append(f"{prefix}: 缺少 input 字段或为空")

            if "scoring_criteria" not in case or not case["scoring_criteria"]:
                errors.append(f"{prefix}: 缺少 scoring_criteria 字段或为空")

            # 难度范围
            difficulty = case.get("difficulty", 1)
            if not isinstance(difficulty, int) or difficulty < 1 or difficulty > 5:
                errors.append(f"{prefix}: difficulty 应在 1-5 之间，当前 {difficulty}")

            # 类别校验
            valid_categories = {"research", "code", "data", "writing", "reasoning", "tool", "general"}
            category = case.get("category", "general")
            if category not in valid_categories:
                errors.append(f"{prefix}: 无效类别 {category}，可选 {valid_categories}")

        return errors
