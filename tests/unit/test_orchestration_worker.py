"""编排 Worker 单元测试（修复-S01 覆盖率提升）。

覆盖 _update_task_status、_invalidate_task_cache 等辅助函数。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.task import Task
from app.workers.tasks import orchestration as orch_module


@pytest_asyncio.fixture
async def orch_db_session(db_engine) -> AsyncGenerator[async_sessionmaker, None]:
    """创建使用临时数据库的 session factory，并 patch orchestration 模块。"""
    factory = async_sessionmaker(
        bind=db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    with patch.object(orch_module, "async_session_factory", factory):
        yield factory


async def _create_task(factory: async_sessionmaker, **kwargs) -> Task:
    """在测试数据库中创建任务。"""
    defaults = dict(
        id=str(uuid.uuid4()),
        input="测试",
        scenario="orchestration",
        status="pending",
        max_rounds=5,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    defaults.update(kwargs)
    task = Task(**defaults)
    async with factory() as db:
        from app.repositories.task_repo import TaskRepository
        repo = TaskRepository(db)
        await repo.create(task)
        await repo.commit()
    return task


class TestUpdateTaskStatus:
    """测试 _update_task_status 辅助函数。"""

    @pytest.mark.asyncio
    async def test_update_to_running_sets_started_at(self, orch_db_session) -> None:
        """测试更新为 running 时设置 started_at。"""
        task = await _create_task(orch_db_session, status="pending")

        await orch_module._update_task_status(task.id, "running")

        async with orch_db_session() as db:
            from app.repositories.task_repo import TaskRepository
            repo = TaskRepository(db)
            updated = await repo.get_by_id(task.id)
            assert updated.status == "running"
            assert updated.started_at is not None

    @pytest.mark.asyncio
    async def test_update_to_completed_sets_completed_at_and_duration(self, orch_db_session) -> None:
        """测试更新为 completed 时设置 completed_at 和 duration_seconds。"""
        started = datetime.now(timezone.utc).replace(tzinfo=None)
        task = await _create_task(orch_db_session, status="running", started_at=started)

        await orch_module._update_task_status(task.id, "completed", result="输出结果")

        async with orch_db_session() as db:
            from app.repositories.task_repo import TaskRepository
            repo = TaskRepository(db)
            updated = await repo.get_by_id(task.id)
            assert updated.status == "completed"
            assert updated.completed_at is not None
            assert updated.duration_seconds is not None
            assert updated.duration_seconds >= 0
            assert updated.result == "输出结果"

    @pytest.mark.asyncio
    async def test_update_to_failed_sets_error(self, orch_db_session) -> None:
        """测试更新为 failed 时设置错误信息。"""
        task = await _create_task(orch_db_session, status="running")

        await orch_module._update_task_status(task.id, "failed", error="something went wrong")

        async with orch_db_session() as db:
            from app.repositories.task_repo import TaskRepository
            repo = TaskRepository(db)
            updated = await repo.get_by_id(task.id)
            assert updated.status == "failed"
            assert updated.error_message == "something went wrong"

    @pytest.mark.asyncio
    async def test_update_nonexistent_task_no_error(self, orch_db_session) -> None:
        """测试更新不存在的任务不抛异常。"""
        # 不应抛异常
        await orch_module._update_task_status("nonexistent-id", "completed")

    @pytest.mark.asyncio
    async def test_update_result_truncated_to_5000(self, orch_db_session) -> None:
        """测试结果文本超过 5000 字符时被截断。"""
        task = await _create_task(orch_db_session, status="running")
        long_result = "x" * 6000

        await orch_module._update_task_status(task.id, "completed", result=long_result)

        async with orch_db_session() as db:
            from app.repositories.task_repo import TaskRepository
            repo = TaskRepository(db)
            updated = await repo.get_by_id(task.id)
            assert len(updated.result) == 5000

    @pytest.mark.asyncio
    async def test_update_error_truncated_to_1000(self, orch_db_session) -> None:
        """测试错误信息超过 1000 字符时被截断。"""
        task = await _create_task(orch_db_session, status="running")
        long_error = "e" * 2000

        await orch_module._update_task_status(task.id, "failed", error=long_error)

        async with orch_db_session() as db:
            from app.repositories.task_repo import TaskRepository
            repo = TaskRepository(db)
            updated = await repo.get_by_id(task.id)
            assert len(updated.error_message) == 1000


class TestInvalidateTaskCache:
    """测试 _invalidate_task_cache 函数。"""

    def test_invalidate_nonexistent_no_error(self) -> None:
        """测试失效不存在的缓存不抛异常（Redis 未连接时走 except 分支）。"""
        # 测试环境无 Redis，应走异常分支不报错
        orch_module._invalidate_task_cache("nonexistent-task-id")

    def test_invalidate_with_redis_mock(self) -> None:
        """测试 Redis 可用时调用 delete。"""
        with patch("redis.from_url") as mock_from_url:
            mock_r = MagicMock()
            mock_from_url.return_value = mock_r
            orch_module._invalidate_task_cache("test-task-123")
            mock_r.delete.assert_called_once_with("task:test-task-123")
            mock_r.close.assert_called_once()


class TestSendCallback:
    """测试 _send_callback 回调函数。"""

    @pytest.mark.asyncio
    async def test_send_callback_success(self) -> None:
        """测试回调发送成功。"""
        with patch("httpx.AsyncClient") as mock_async_client:
            mock_client = AsyncMock()
            mock_async_client.return_value.__aenter__.return_value = mock_client
            mock_resp = MagicMock(status_code=200)
            mock_client.post.return_value = mock_resp

            await orch_module._send_callback(
                "https://example.com/cb",
                {"task_id": "t1", "status": "completed", "final_output": "done"},
            )
            mock_client.post.assert_called_once()
            call_kwargs = mock_client.post.call_args
            assert call_kwargs.kwargs.get("headers", {}).get("X-Task-Id") == "t1"

    @pytest.mark.asyncio
    async def test_send_callback_retry_then_fail(self) -> None:
        """测试回调失败后重试，最终耗尽。"""
        with patch("httpx.AsyncClient") as mock_async_client:
            mock_client = AsyncMock()
            mock_async_client.return_value.__aenter__.return_value = mock_client
            mock_resp = MagicMock(status_code=500)
            mock_client.post.return_value = mock_resp

            # patch asyncio.sleep 避免实际等待
            with patch("app.workers.tasks.orchestration.asyncio.sleep", new_callable=AsyncMock):
                await orch_module._send_callback(
                    "https://example.com/cb",
                    {"task_id": "t1", "status": "completed"},
                )
            # 最多 4 次尝试（1 首次 + 3 重试）
            assert mock_client.post.call_count == 4
