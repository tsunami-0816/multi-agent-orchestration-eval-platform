"""任务接口（T8-3/T8-4/T8-5/T8-6）。

提供任务的完整 CRUD 和操作接口：
- POST /tasks: 创建任务
- GET /tasks: 任务列表（分页）
- GET /tasks/{task_id}: 任务详情
- POST /tasks/{task_id}/cancel: 取消任务（T8-5）
- GET /tasks/{task_id}/trace: 任务轨迹/消息历史（T8-4）
- GET /tasks/{task_id}/messages: 消息列表
- GET /tasks/{task_id}/executions: 执行记录

架构决策（T-01）：RESTful 风格，统一响应格式，
异步执行长耗时任务，API 立即返回 task_id。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.core.exceptions import AppException, ErrorCode
from app.schemas.common import ApiResponse
from app.schemas.task import (
    TaskCancelResponse,
    TaskCreateRequest,
    TaskListResponse,
    TaskMessageResponse,
    TaskResponse,
    TaskTraceResponse,
)
from app.services.task_service import TaskService

router = APIRouter(prefix="/tasks", tags=["任务管理"])

_task_service = TaskService()


@router.post("", response_model=ApiResponse[TaskResponse], summary="创建任务")
async def create_task(
    request: Request,
    task_request: TaskCreateRequest,
    db: AsyncSession = Depends(get_db_session),
) -> ApiResponse[TaskResponse]:
    """创建并提交异步任务（T8-3）。

    - 输入验证后写入数据库
    - 提交 Celery 任务异步执行
    - 立即返回任务 ID，客户端轮询状态
    """
    task = await _task_service.create_task(
        user_input=task_request.input,
        task_type=task_request.scenario,
        callback_url=task_request.callback_url,
        max_rounds=task_request.max_rounds,
    )
    # 修复Bug-003 改动原因：使用success()填充request_id和timestamp，与健康检查接口保持一致
    return ApiResponse.success(data=TaskResponse.model_validate(task), request_id=getattr(request.state, "trace_id", ""))


@router.get("", response_model=ApiResponse[TaskListResponse], summary="任务列表")
async def list_tasks(
    request: Request,
    status: str | None = Query(default=None, description="按状态筛选"),
    page: int = Query(default=1, ge=1, description="页码"),
    page_size: int = Query(default=20, ge=1, le=100, description="每页数量"),
    db: AsyncSession = Depends(get_db_session),
) -> ApiResponse[TaskListResponse]:
    """分页查询任务列表（T8-3）。"""
    tasks, total = await _task_service.list_tasks(status=status, page=page, page_size=page_size)
    # 修复Bug-003 改动原因：使用success()填充request_id和timestamp
    return ApiResponse.success(data=TaskListResponse(
        items=[TaskResponse.model_validate(t) for t in tasks],
        total=total,
        page=page,
        page_size=page_size,
    ), request_id=getattr(request.state, "trace_id", ""))


@router.get("/{task_id}", response_model=ApiResponse[TaskResponse], summary="任务详情")
async def get_task(
    request: Request,
    task_id: str,
    db: AsyncSession = Depends(get_db_session),
) -> ApiResponse[TaskResponse]:
    """获取任务详情（T8-3）。"""
    task = await _task_service.get_task(task_id)
    # 修复Bug-003 改动原因：使用success()填充request_id和timestamp
    return ApiResponse.success(data=TaskResponse.model_validate(task), request_id=getattr(request.state, "trace_id", ""))


@router.post("/{task_id}/cancel", response_model=ApiResponse[TaskCancelResponse], summary="取消任务")
async def cancel_task(
    request: Request,
    task_id: str,
    db: AsyncSession = Depends(get_db_session),
) -> ApiResponse[TaskCancelResponse]:
    """取消正在运行的任务（T8-5）。

    设置数据库取消标志，Agent 节点在每步执行前检查，
    发现取消后安全停止，保证数据一致性。
    """
    task = await _task_service.cancel_task(task_id)
    # 修复Bug-003 改动原因：使用success()填充request_id和timestamp
    return ApiResponse.success(data=TaskCancelResponse(
        id=task.id,
        status=task.status,
        message="任务已取消" if task.status == "cancelled" else "取消请求已提交",
    ), request_id=getattr(request.state, "trace_id", ""))


@router.post("/{task_id}/resume", response_model=ApiResponse[TaskResponse], summary="恢复任务")
async def resume_task(
    request: Request,
    task_id: str,
    db: AsyncSession = Depends(get_db_session),
) -> ApiResponse[TaskResponse]:
    """修复-BUG-022 恢复已失败或已取消的任务（T8-5）。

    验证任务状态为FAILED/CANCELLED，重置状态为pending，
    清除错误信息，提交Celery任务重新执行。
    COMPLETED状态任务返回409。
    """
    task = await _task_service.resume_task(task_id)
    # 修复Bug-003 改动原因：使用success()填充request_id和timestamp
    return ApiResponse.success(data=TaskResponse.model_validate(task), request_id=getattr(request.state, "trace_id", ""))


@router.get("/{task_id}/trace", response_model=ApiResponse[TaskTraceResponse], summary="任务轨迹")
async def get_task_trace(
    request: Request,
    task_id: str,
    limit: int = Query(default=100, ge=1, le=1000, description="最大消息数"),
    db: AsyncSession = Depends(get_db_session),
) -> ApiResponse[TaskTraceResponse]:
    """获取任务完整轨迹（Agent 消息历史）（T8-4）。

    返回任务执行过程中产生的全部 Agent 消息，
    包括 Planner/Executor/Reviewer/Aggregator 各节点的输出。
    修复-BUG-023: 使用TaskMessageResponse而非TaskResponse验证消息
    修复-BUG-024: 返回检查点快照列表
    """
    # 先验证任务存在
    await _task_service.get_task(task_id)
    messages = await _task_service.get_task_messages(task_id, limit=limit)

    # 修复-BUG-024 查询检查点快照列表
    checkpoints = []
    try:
        from app.agent.orchestrator import AgentOrchestrator
        checkpoint_files = AgentOrchestrator.list_checkpoints(task_id)
        for cf in checkpoint_files:
            try:
                import json
                data = json.loads(cf.read_text(encoding="utf-8"))
                checkpoints.append({
                    "checkpoint_id": cf.stem,
                    "file_name": cf.name,
                    "node": data.get("_node", ""),
                    "round_number": data.get("round_count", 0),
                    "status": data.get("status", ""),
                })
            except Exception:
                checkpoints.append({
                    "checkpoint_id": cf.stem,
                    "file_name": cf.name,
                    "node": "",
                    "round_number": 0,
                    "status": "",
                })
    except Exception as e:
        from app.core.logging import get_logger
        get_logger(__name__).warning("trace_checkpoints_load_failed", task_id=task_id, error=str(e))

    # 修复Bug-003 改动原因：使用success()填充request_id和timestamp
    return ApiResponse.success(data=TaskTraceResponse(
        task_id=task_id,
        # 修复-BUG-023 使用正确的TaskMessageResponse验证AgentMessage
        messages=[TaskMessageResponse.model_validate(m) for m in messages],
        total=len(messages),
        checkpoints=checkpoints,
    ), request_id=getattr(request.state, "trace_id", ""))


@router.get("/{task_id}/messages", summary="任务消息列表")
async def get_task_messages(
    request: Request,
    task_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db_session),
) -> ApiResponse[list[dict]]:
    """获取任务的 Agent 消息列表（T8-4）。"""
    await _task_service.get_task(task_id)
    messages = await _task_service.get_task_messages(task_id, limit=limit)
    # 转换为字典
    result = []
    for m in messages:
        result.append({
            "id": m.id,
            "role": m.role,
            # 修复Bug-REG2-003 改动原因：AgentMessage模型无node字段，原m.node导致AttributeError返回500；
            # 补充模型实际存在的agent_role/message_type/round_number字段
            "agent_role": m.agent_role,
            "message_type": m.message_type,
            "round_number": m.round_number,
            "content": m.content,
            "sequence": m.sequence,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        })
    # 修复Bug-003 改动原因：使用success()填充request_id和timestamp
    return ApiResponse.success(data=result, request_id=getattr(request.state, "trace_id", ""))


@router.get("/{task_id}/executions", summary="任务执行记录")
async def get_task_executions(
    request: Request,
    task_id: str,
    db: AsyncSession = Depends(get_db_session),
) -> ApiResponse[list[dict]]:
    """获取任务的执行记录（T8-4）。

    修复-NEW-001: 修正字段映射，TaskExecution模型实际字段为
    agent_role/node_name/input_data/output_data/round_number/created_at
    """
    await _task_service.get_task(task_id)
    executions = await _task_service.get_task_executions(task_id)
    result = []
    for e in executions:
        result.append({
            "id": e.id,
            "task_id": e.task_id,
            "agent_role": e.agent_role,  # 修复-NEW-001 原e.status不存在
            "node": e.node_name,  # 修复-NEW-001 原e.node不存在
            "input": e.input_data[:200] if e.input_data else "",  # 修复-NEW-001 原e.input不存在
            "output": e.output_data[:500] if e.output_data else "",  # 修复-NEW-001 原e.output不存在
            "round_number": e.round_number,  # 修复-NEW-001 新增轮次字段
            "created_at": e.created_at.isoformat() if e.created_at else None,  # 修复-NEW-001 原started_at/completed_at不存在
        })
    # 修复Bug-003 改动原因：使用success()填充request_id和timestamp
    return ApiResponse.success(data=result, request_id=getattr(request.state, "trace_id", ""))
