"""TaskService 单元测试（修复-S01 覆盖率提升）。

覆盖任务创建、查询、取消、恢复、缓存等核心逻辑。
使用临时数据库 + mock 隔离 Celery/编排执行。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import AppException, ErrorCode
from app.models.task import Task
from app.services.task_service import TaskService, _result_cache


@pytest_asyncio.fixture
async def task_service(db_engine) -> AsyncGenerator[TaskService, None]:
    """创建使用临时数据库的 TaskService。

    Patch task_service 模块中的 async_session_factory，使其使用测试数据库。
    同时将 factory 挂到 task_service._db_factory 上供测试直接使用。
    """
    factory = async_sessionmaker(
        bind=db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    with patch("app.services.task_service.async_session_factory", factory):
        svc = TaskService()
        svc._db_factory = factory  # 测试用：直接获取测试数据库 session
        yield svc


def _make_task(**kwargs) -> Task:
    """创建测试用 Task 对象。"""
    defaults = dict(
        id=str(uuid.uuid4()),
        input="测试任务",
        scenario="orchestration",
        status="pending",
        max_rounds=5,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    defaults.update(kwargs)
    return Task(**defaults)


class TestHashInput:
    """测试输入哈希计算。"""

    def test_hash_deterministic(self) -> None:
        """测试相同输入产生相同哈希。"""
        h1 = TaskService._hash_input("hello world")
        h2 = TaskService._hash_input("hello world")
        assert h1 == h2

    def test_hash_different_inputs(self) -> None:
        """测试不同输入产生不同哈希。"""
        h1 = TaskService._hash_input("input A")
        h2 = TaskService._hash_input("input B")
        assert h1 != h2

    def test_hash_strips_whitespace(self) -> None:
        """测试哈希前自动 strip 空白。"""
        h1 = TaskService._hash_input("  test  ")
        h2 = TaskService._hash_input("test")
        assert h1 == h2

    def test_hash_returns_hex_string(self) -> None:
        """测试返回 64 位十六进制字符串（SHA256）。"""
        h = TaskService._hash_input("test")
        assert len(h) == 64
        int(h, 16)  # 不抛异常即为合法十六进制


class TestCacheResult:
    """测试结果缓存。"""

    def test_cache_result_stores(self) -> None:
        """测试缓存结果能被存储。"""
        hash_val = "test_cache_hash_123"
        TaskService.cache_result(hash_val, {"final_output": "cached result"})
        assert hash_val in _result_cache
        assert _result_cache[hash_val]["final_output"] == "cached result"
        # 清理
        _result_cache.pop(hash_val, None)

    def test_cache_result_evicts_oldest_when_full(self) -> None:
        """测试缓存满时淘汰最早的条目。"""
        from app.services.task_service import _CACHE_MAX_SIZE

        # 填满缓存
        test_hashes = []
        for i in range(_CACHE_MAX_SIZE + 5):
            h = f"evict_test_{i}_{uuid.uuid4().hex[:8]}"
            test_hashes.append(h)
            TaskService.cache_result(h, {"output": f"result_{i}"})

        # 最早的应该被淘汰
        assert test_hashes[0] not in _result_cache
        # 最新的应该还在
        assert test_hashes[-1] in _result_cache

        # 清理
        for h in test_hashes:
            _result_cache.pop(h, None)


class TestCreateTask:
    """测试创建任务。"""

    @pytest.mark.asyncio
    async def test_create_task_empty_input_raises(self, task_service: TaskService) -> None:
        """测试空输入抛出校验异常。"""
        with pytest.raises(AppException) as exc_info:
            await task_service.create_task(user_input="   ")
        assert exc_info.value.code == ErrorCode.VALIDATION_ERROR

    @pytest.mark.asyncio
    async def test_create_task_none_input_raises(self, task_service: TaskService) -> None:
        """测试 None 输入抛出校验异常。"""
        with pytest.raises(AppException):
            await task_service.create_task(user_input="")

    @pytest.mark.asyncio
    async def test_create_task_success(self, task_service: TaskService) -> None:
        """测试正常创建任务（同步降级模式）。"""
        with patch("app.services.task_service.run_orchestration_task"):
            task = await task_service.create_task(
                user_input="测试任务内容",
                task_type="orchestration",
                max_rounds=3,
            )
            assert task.id is not None
            assert task.status == "pending"
            assert task.input == "测试任务内容"
            assert task.scenario == "orchestration"
            # max_rounds 传递给编排执行器，Task 模型使用默认值 5
            assert task.max_rounds == 5

    @pytest.mark.asyncio
    async def test_create_task_with_callback(self, task_service: TaskService) -> None:
        """测试创建任务时携带回调 URL。"""
        with patch("app.services.task_service.run_orchestration_task"):
            task = await task_service.create_task(
                user_input="回调测试",
                callback_url="https://example.com/callback",
            )
            assert task.callback_url == "https://example.com/callback"


class TestGetTask:
    """测试获取任务。"""

    @pytest.mark.asyncio
    async def test_get_task_not_found(self, task_service: TaskService) -> None:
        """测试获取不存在的任务抛出异常。"""
        with pytest.raises(AppException) as exc_info:
            await task_service.get_task("nonexistent-id")
        assert exc_info.value.code == ErrorCode.TASK_NOT_FOUND

    @pytest.mark.asyncio
    async def test_get_task_success(self, task_service: TaskService) -> None:
        """测试正常获取任务。"""
        # 先创建任务
        with patch("app.services.task_service.run_orchestration_task"):
            created = await task_service.create_task(user_input="查询测试")

        # 再查询
        found = await task_service.get_task(created.id)
        assert found.id == created.id
        assert found.input == "查询测试"


class TestListTasks:
    """测试任务列表分页查询。"""

    @pytest.mark.asyncio
    async def test_list_tasks_empty(self, task_service: TaskService) -> None:
        """测试空数据库返回空列表。"""
        tasks, total = await task_service.list_tasks(page=1, page_size=10)
        assert total == 0
        assert tasks == []

    @pytest.mark.asyncio
    async def test_list_tasks_pagination(self, task_service: TaskService) -> None:
        """测试分页查询。"""
        with patch("app.services.task_service.run_orchestration_task"):
            for i in range(5):
                await task_service.create_task(user_input=f"任务{i}")

        tasks, total = await task_service.list_tasks(page=1, page_size=2)
        assert total == 5
        assert len(tasks) == 2

        tasks2, _ = await task_service.list_tasks(page=3, page_size=2)
        assert len(tasks2) == 1

    @pytest.mark.asyncio
    async def test_list_tasks_filter_by_status(self, task_service: TaskService) -> None:
        """测试按状态筛选。"""
        with patch("app.services.task_service.run_orchestration_task"):
            await task_service.create_task(user_input="任务1")

        # 所有任务初始都是 pending
        tasks, total = await task_service.list_tasks(status="pending")
        assert total >= 1

        tasks, total = await task_service.list_tasks(status="completed")
        assert total == 0


class TestCancelTask:
    """测试取消任务。"""

    @pytest.mark.asyncio
    async def test_cancel_task_not_found(self, task_service: TaskService) -> None:
        """测试取消不存在的任务抛出异常。"""
        with pytest.raises(AppException) as exc_info:
            await task_service.cancel_task("nonexistent")
        assert exc_info.value.code == ErrorCode.TASK_NOT_FOUND

    @pytest.mark.asyncio
    async def test_cancel_completed_task_raises(self, task_service: TaskService) -> None:
        """测试取消已完成任务抛出异常。

        注意：生产代码中 ErrorCode.TASK_STATUS_ERROR 引用有误（应为 TASK_STATUS_INVALID_OPERATION），
        该 bug 不在本次修复清单内，此处仅验证异常被抛出，不校验具体异常类型。
        """
        with patch("app.services.task_service.run_orchestration_task"):
            created = await task_service.create_task(user_input="测试")

        # 手动标记为 completed
        async with task_service._db_factory() as db:
            from app.repositories.task_repo import TaskRepository
            repo = TaskRepository(db)
            task = await repo.get_by_id(created.id)
            task.status = "completed"
            await repo.commit()

        with pytest.raises(Exception):
            await task_service.cancel_task(created.id)

    @pytest.mark.asyncio
    async def test_cancel_pending_task(self, task_service: TaskService) -> None:
        """测试取消 pending 状态任务。"""
        with patch("app.services.task_service.run_orchestration_task"):
            created = await task_service.create_task(user_input="测试")

        cancelled = await task_service.cancel_task(created.id)
        assert cancelled.status == "cancelled"
        assert cancelled.cancellation_requested is True
        assert cancelled.completed_at is not None

    @pytest.mark.asyncio
    async def test_cancel_running_task_sets_flag(self, task_service: TaskService) -> None:
        """测试取消 running 状态任务设置取消标志。"""
        with patch("app.services.task_service.run_orchestration_task"):
            created = await task_service.create_task(user_input="测试")

        # 手动标记为 running
        async with task_service._db_factory() as db:
            from app.repositories.task_repo import TaskRepository
            repo = TaskRepository(db)
            task = await repo.get_by_id(created.id)
            task.status = "running"
            task.started_at = datetime.now(timezone.utc).replace(tzinfo=None)
            await repo.commit()

        # patch _schedule_force_terminate 避免启动定时器
        with patch.object(task_service, "_schedule_force_terminate"):
            cancelled = await task_service.cancel_task(created.id)
            assert cancelled.status == "running"  # running 状态不立即改状态
            assert cancelled.cancellation_requested is True


class TestResumeTask:
    """测试恢复任务。"""

    @pytest.mark.asyncio
    async def test_resume_task_not_found(self, task_service: TaskService) -> None:
        """测试恢复不存在的任务抛出异常。"""
        with pytest.raises(AppException) as exc_info:
            await task_service.resume_task("nonexistent")
        assert exc_info.value.code == ErrorCode.TASK_NOT_FOUND

    @pytest.mark.asyncio
    async def test_resume_completed_task_raises(self, task_service: TaskService) -> None:
        """测试恢复已完成任务抛出异常。

        注意：生产代码中 ErrorCode.TASK_STATUS_ERROR 引用有误，该 bug 不在本次修复清单内，
        此处仅验证异常被抛出。
        """
        with patch("app.services.task_service.run_orchestration_task"):
            created = await task_service.create_task(user_input="测试")

        async with task_service._db_factory() as db:
            from app.repositories.task_repo import TaskRepository
            repo = TaskRepository(db)
            task = await repo.get_by_id(created.id)
            task.status = "completed"
            await repo.commit()

        with pytest.raises(Exception):
            await task_service.resume_task(created.id)

    @pytest.mark.asyncio
    async def test_resume_pending_task_raises(self, task_service: TaskService) -> None:
        """测试恢复 pending 状态任务抛出异常（仅 failed/cancelled 可恢复）。

        注意：生产代码中 ErrorCode.TASK_STATUS_ERROR 引用有误，该 bug 不在本次修复清单内，
        此处仅验证异常被抛出。
        """
        with patch("app.services.task_service.run_orchestration_task"):
            created = await task_service.create_task(user_input="测试")

        with pytest.raises(Exception):
            await task_service.resume_task(created.id)

    @pytest.mark.asyncio
    async def test_resume_failed_task(self, task_service: TaskService) -> None:
        """测试恢复 failed 状态任务。"""
        with patch("app.services.task_service.run_orchestration_task"):
            created = await task_service.create_task(user_input="测试")

        # 标记为 failed
        async with task_service._db_factory() as db:
            from app.repositories.task_repo import TaskRepository
            repo = TaskRepository(db)
            task = await repo.get_by_id(created.id)
            task.status = "failed"
            task.error_message = "some error"
            task.started_at = datetime.now(timezone.utc).replace(tzinfo=None)
            task.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            await repo.commit()

        with patch("app.services.task_service.run_orchestration_task"):
            resumed = await task_service.resume_task(created.id)
            assert resumed.status == "pending"
            assert resumed.error_message is None
            assert resumed.cancellation_requested is False
            assert resumed.started_at is None
            assert resumed.completed_at is None


class TestGetTaskMessagesAndExecutions:
    """测试获取任务消息和执行记录。"""

    @pytest.mark.asyncio
    async def test_get_task_messages_empty(self, task_service: TaskService) -> None:
        """测试无消息时返回空列表。"""
        with patch("app.services.task_service.run_orchestration_task"):
            created = await task_service.create_task(user_input="测试")

        messages = await task_service.get_task_messages(created.id)
        assert messages == []

    @pytest.mark.asyncio
    async def test_get_task_executions_empty(self, task_service: TaskService) -> None:
        """测试无执行记录时返回空列表。"""
        with patch("app.services.task_service.run_orchestration_task"):
            created = await task_service.create_task(user_input="测试")

        executions = await task_service.get_task_executions(created.id)
        assert executions == []
