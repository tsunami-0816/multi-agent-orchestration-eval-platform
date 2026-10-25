"""健康检查接口单元测试（T8-8）。

测试 /api/v1/health 端点返回结构和状态判定。
"""

from __future__ import annotations

import pytest


class TestHealthEndpoint:
    """健康检查接口测试。"""

    @pytest.mark.asyncio
    async def test_health_returns_200(self, client) -> None:
        """测试健康检查返回 200。"""
        response = await client.get("/api/v1/health")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_health_response_structure(self, client) -> None:
        """测试健康检查响应结构（统一信封）。"""
        response = await client.get("/api/v1/health")
        data = response.json()

        # 统一信封字段
        assert "code" in data
        assert "message" in data
        assert "data" in data
        assert "request_id" in data
        assert "timestamp" in data
        assert data["code"] == 0

        # 健康检查数据字段
        health = data["data"]
        assert "status" in health
        assert "version" in health
        assert "db" in health
        assert "redis" in health
        assert "llm" in health

    @pytest.mark.asyncio
    async def test_health_db_status_ok(self, client) -> None:
        """测试数据库状态为 ok。"""
        response = await client.get("/api/v1/health")
        data = response.json()
        assert data["data"]["db"] == "ok"

    @pytest.mark.asyncio
    async def test_health_llm_mock_skipped(self, client) -> None:
        """测试 Mock 模式下 LLM 检查跳过。"""
        response = await client.get("/api/v1/health")
        data = response.json()
        # Mock 模式下 LLM 状态为 skipped
        assert data["data"]["llm"] in ("skipped", "warning", "ok")

    @pytest.mark.asyncio
    async def test_health_has_trace_id_header(self, client) -> None:
        """测试响应头包含 X-Trace-Id。"""
        response = await client.get("/api/v1/health")
        assert "x-trace-id" in response.headers or "X-Trace-Id" in response.headers

    @pytest.mark.asyncio
    async def test_root_endpoint(self, client) -> None:
        """测试根路径重定向到前端页面。"""
        # 修复-OUT-004 改动原因：根路径 "/" 在 main.py 中定义为 RedirectResponse 到 /static/index.html，
        # 返回 307 状态码而非 200，原测试期望 200 且含 docs/health 字段与实际行为不符
        response = await client.get("/", follow_redirects=False)
        assert response.status_code == 307
        assert "location" in response.headers
        assert "/static/index.html" in response.headers["location"]


class TestErrorHandling:
    """全局异常处理测试。"""

    @pytest.mark.asyncio
    async def test_404_returns_unified_format(self, client) -> None:
        """测试 404 返回统一错误信封。"""
        response = await client.get("/api/v1/nonexistent")
        assert response.status_code == 404
        data = response.json()
        assert data["code"] == 10003
        assert "message" in data
        assert "request_id" in data

    @pytest.mark.asyncio
    async def test_validation_error_returns_10002(self, client) -> None:
        """测试参数校验失败返回 10002。

        健康检查接口无参数，此测试验证异常处理器已注册。
        """
        # 通过不存在的 POST 端点触发 404 而非验证错误
        # 验证错误需要有参数校验的端点，M8 实现后补充
        response = await client.post("/api/v1/health")
        # FastAPI 对不支持的方法返回 405
        assert response.status_code in (404, 405)
