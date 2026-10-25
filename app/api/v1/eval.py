"""评测接口（T8-7）。

提供评测运行的完整管理接口：
- POST /eval/runs: 创建评测运行
- GET /eval/runs: 评测运行列表
- GET /eval/runs/{run_id}: 运行详情
- GET /eval/runs/{run_id}/scores: 评分记录
- GET /eval/runs/{run_id}/report: 评测报告
- GET /eval/runs/{run_id}/report/download: 下载报告
- POST /eval/runs/{run_id}/check: 触发完成检测
- GET /eval/runs/{run_id}/consistency: 一致性校验
- GET /eval/cases: 用例列表
- POST /eval/cases/import: 导入用例

架构决策（T-01）：评测运行为异步执行，
创建后立即返回 run_id，客户端轮询状态或等待完成。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.eval.eval_service import EvalService
from app.eval.loader import EvalCaseLoader
from app.report.storage import ReportStorage
from app.schemas.common import ApiResponse
from app.schemas.eval import (
    EvalCaseImportResponse,
    EvalCaseResponse,
    EvalConsistencyResponse,
    EvalReportResponse,
    EvalRunCreateRequest,
    EvalRunListResponse,
    EvalRunResponse,
    EvalScoreResponse,
)

router = APIRouter(prefix="/eval", tags=["评测管理"])

_eval_service = EvalService()
_case_loader = EvalCaseLoader()
_report_storage = ReportStorage()


@router.post("/runs", response_model=ApiResponse[EvalRunResponse], summary="创建评测运行")
async def create_eval_run(
    request: Request,
    eval_request: EvalRunCreateRequest,
    db: AsyncSession = Depends(get_db_session),
) -> ApiResponse[EvalRunResponse]:
    """创建并启动评测运行（T8-7）。

    - 选择用例集（全部/指定ID/按类别）
    - 创建运行记录
    - 提交 Celery 任务逐条执行用例
    """
    run = await _eval_service.create_run(
        name=eval_request.name,
        model_config=eval_request.model_settings,
        case_ids=eval_request.case_ids,
        category=eval_request.category,
    )
    # 修复Bug-003 改动原因：使用success()填充request_id和timestamp，与健康检查接口保持一致
    return ApiResponse.success(data=EvalRunResponse.model_validate(run), request_id=getattr(request.state, "trace_id", ""))


@router.get("/runs", response_model=ApiResponse[EvalRunListResponse], summary="评测运行列表")
async def list_eval_runs(
    request: Request,
    status: str | None = Query(default=None, description="按状态筛选"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db_session),
) -> ApiResponse[EvalRunListResponse]:
    """分页查询评测运行列表（T8-7）。"""
    runs, total = await _eval_service.list_runs(status=status, page=page, page_size=page_size)
    # 修复Bug-003 改动原因：使用success()填充request_id和timestamp
    return ApiResponse.success(data=EvalRunListResponse(
        items=[EvalRunResponse.model_validate(r) for r in runs],
        total=total,
        page=page,
        page_size=page_size,
    ), request_id=getattr(request.state, "trace_id", ""))


@router.get("/runs/{run_id}", response_model=ApiResponse[EvalRunResponse], summary="运行详情")
async def get_eval_run(
    request: Request,
    run_id: str,
    db: AsyncSession = Depends(get_db_session),
) -> ApiResponse[EvalRunResponse]:
    """获取评测运行详情（T8-7）。"""
    run = await _eval_service.get_run(run_id)
    # 修复Bug-003 改动原因：使用success()填充request_id和timestamp
    return ApiResponse.success(data=EvalRunResponse.model_validate(run), request_id=getattr(request.state, "trace_id", ""))


@router.get("/runs/{run_id}/scores", response_model=ApiResponse[list[EvalScoreResponse]], summary="评分记录")
async def get_run_scores(
    request: Request,
    run_id: str,
    db: AsyncSession = Depends(get_db_session),
) -> ApiResponse[list[EvalScoreResponse]]:
    """获取运行的全部评分记录（T8-7）。"""
    await _eval_service.get_run(run_id)  # 验证存在
    scores = await _eval_service.get_run_scores(run_id)
    # 修复Bug-003 改动原因：使用success()填充request_id和timestamp
    return ApiResponse.success(data=[EvalScoreResponse.model_validate(s) for s in scores], request_id=getattr(request.state, "trace_id", ""))


@router.get("/runs/{run_id}/report", response_model=ApiResponse[EvalReportResponse], summary="评测报告")
async def get_eval_report(
    request: Request,
    run_id: str,
    db: AsyncSession = Depends(get_db_session),
) -> ApiResponse[EvalReportResponse]:
    """获取评测报告（T8-7/T7-5）。

    返回报告元数据和下载链接。
    """
    import json

    run = await _eval_service.get_run(run_id)
    json_content = _report_storage.read_json(run_id)
    summary = {}
    if json_content:
        try:
            data = json.loads(json_content)
            summary = data.get("summary", {})
        except json.JSONDecodeError:
            pass

    # 修复Bug-003 改动原因：使用success()填充request_id和timestamp
    return ApiResponse.success(data=EvalReportResponse(
        run_id=run_id,
        summary=summary,
        html_url=f"/api/v1/eval/runs/{run_id}/report/download?format=html",
        json_url=f"/api/v1/eval/runs/{run_id}/report/download?format=json",
        generated_at=run.completed_at,
    ), request_id=getattr(request.state, "trace_id", ""))


@router.get("/runs/{run_id}/report/download", summary="下载报告")
async def download_report(
    run_id: str,
    format: str = Query(default="html", description="格式：html/json"),
    db: AsyncSession = Depends(get_db_session),
):
    """下载评测报告文件（T8-7/T7-5）。"""
    await _eval_service.get_run(run_id)  # 验证存在

    if format == "json":
        file_path = _report_storage.get_json_path(run_id)
        media_type = "application/json"
        filename = f"report_{run_id}.json"
    else:
        file_path = _report_storage.get_html_path(run_id)
        media_type = "text/html"
        filename = f"report_{run_id}.html"

    if not file_path:
        return JSONResponse(
            status_code=404,
            content={"code": 10004, "message": "报告文件不存在", "data": None},
        )

    return FileResponse(path=str(file_path), media_type=media_type, filename=filename)


@router.post("/runs/{run_id}/check", response_model=ApiResponse[EvalRunResponse], summary="触发完成检测")
async def check_run_completion(
    request: Request,
    run_id: str,
    db: AsyncSession = Depends(get_db_session),
) -> ApiResponse[EvalRunResponse]:
    """手动触发完成检测（T6-6/T8-7）。

    当全部用例完成时，标记运行完成并生成报告。
    """
    run = await _eval_service.check_completion(run_id)
    if run is None:
        # 未完成，返回当前状态
        run = await _eval_service.get_run(run_id)
    # 修复Bug-003 改动原因：使用success()填充request_id和timestamp
    return ApiResponse.success(data=EvalRunResponse.model_validate(run), request_id=getattr(request.state, "trace_id", ""))


@router.get("/runs/{run_id}/consistency", response_model=ApiResponse[EvalConsistencyResponse], summary="一致性校验")
async def check_consistency(
    request: Request,
    run_id: str,
    db: AsyncSession = Depends(get_db_session),
) -> ApiResponse[EvalConsistencyResponse]:
    """校验评分记录与运行统计的一致性（T6-7/T8-7）。"""
    await _eval_service.get_run(run_id)  # 验证存在
    result = await _eval_service.validate_consistency(run_id)
    # 修复Bug-003 改动原因：使用success()填充request_id和timestamp
    return ApiResponse.success(data=EvalConsistencyResponse(
        run_id=run_id,
        passed=result["passed"],
        errors=result["errors"],
        details=result["details"],
    ), request_id=getattr(request.state, "trace_id", ""))


@router.get("/cases", response_model=ApiResponse[list[EvalCaseResponse]], summary="用例列表")
async def list_eval_cases(
    request: Request,
    category: str | None = Query(default=None, description="按类别筛选"),
    difficulty: int | None = Query(default=None, ge=1, le=5, description="按难度筛选"),
    db: AsyncSession = Depends(get_db_session),
) -> ApiResponse[list[EvalCaseResponse]]:
    """查询评测用例列表（T8-7）。"""
    cases = await _case_loader.get_cases(category=category, difficulty=difficulty)
    # 修复Bug-003 改动原因：使用success()填充request_id和timestamp
    return ApiResponse.success(data=[EvalCaseResponse.model_validate(c) for c in cases], request_id=getattr(request.state, "trace_id", ""))


@router.post("/cases/import", response_model=ApiResponse[EvalCaseImportResponse], summary="导入用例")
async def import_eval_cases(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> ApiResponse[EvalCaseImportResponse]:
    """从 YAML 文件导入评测用例到数据库（T8-7）。

    幂等导入（INSERT OR IGNORE），已存在的用例跳过。
    """
    cases = _case_loader.load_from_yaml()
    errors = _case_loader.validate_cases(cases)
    imported, total = await _case_loader.import_to_db(cases)
    # 修复Bug-003 改动原因：使用success()填充request_id和timestamp
    return ApiResponse.success(data=EvalCaseImportResponse(
        imported=imported,
        total=total,
        errors=errors,
    ), request_id=getattr(request.state, "trace_id", ""))
