"""评测相关 Pydantic 模型（T8-2）。

定义评测接口的请求和响应数据结构。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class EvalRunCreateRequest(BaseModel):
    """创建评测运行请求（T8-7）。"""

    # 修复-OUT-005 改动原因：model_settings 以 model_ 开头触发 Pydantic v2 protected namespace 警告，
    # 通过 protected_namespaces=() 禁用检查，字段名和 API 契约保持不变
    model_config = ConfigDict(protected_namespaces=())

    name: str = Field(..., min_length=1, max_length=200, description="评测运行名称")
    model_settings: dict[str, Any] = Field(default_factory=dict, description="模型配置")
    case_ids: Optional[list[str]] = Field(default=None, description="指定用例 ID 列表")
    category: Optional[str] = Field(default=None, description="按类别筛选：research/code/data/writing/reasoning/tool")


class EvalRunResponse(BaseModel):
    """评测运行响应（T8-7）。"""

    # 修复-OUT-005 改动原因：model_config_json 以 model_ 开头触发 Pydantic v2 protected namespace 警告
    model_config = ConfigDict(from_attributes=True, populate_by_name=True, protected_namespaces=())

    id: str
    name: str
    model_config_json: str = Field(alias="model_config")
    total_cases: int
    completed_cases: int
    passed_cases: int
    # 修复-OUT-001 改动原因：ORM 模型 EvalRun 已有 failed_cases 列，API Schema 缺少该字段导致数据已存储但未暴露
    failed_cases: int = 0
    avg_score: Optional[float] = None
    status: str
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class EvalRunListResponse(BaseModel):
    """评测运行列表响应（T8-7）。"""

    items: list[EvalRunResponse]
    total: int
    page: int
    page_size: int


class EvalCaseResponse(BaseModel):
    """评测用例响应。"""

    id: str
    category: str
    input: str
    scoring_criteria: str
    difficulty: int
    enabled: bool

    model_config = {"from_attributes": True}


class EvalScoreResponse(BaseModel):
    """评测评分响应。"""

    id: int
    run_id: str
    case_id: str
    accuracy_score: Optional[float] = None
    relevance_score: Optional[float] = None
    completeness_score: Optional[float] = None
    format_score: Optional[float] = None
    overall_score: Optional[float] = None
    judge_feedback: Optional[str] = None
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class EvalReportResponse(BaseModel):
    """评测报告响应（T8-7）。"""

    run_id: str
    summary: dict[str, Any]
    html_url: str
    json_url: str
    generated_at: Optional[datetime] = None


class EvalConsistencyResponse(BaseModel):
    """一致性校验响应（T6-7/T8-7）。"""

    run_id: str
    passed: bool
    errors: list[str]
    details: dict[str, Any]


class EvalCaseImportResponse(BaseModel):
    """用例导入响应。"""

    imported: int
    total: int
    errors: list[str]
