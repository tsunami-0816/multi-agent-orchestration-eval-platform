"""可观测性指标单元测试（修复-S01 覆盖率提升）。

覆盖 MetricsCollector 的计数器、仪表盘、直方图、快照和 Prometheus 格式输出。
"""

from __future__ import annotations

import time

import pytest

from app.observability.metrics import MetricsCollector, get_metrics


class TestMetricsCollector:
    """测试指标收集器。"""

    def test_incr_counter(self) -> None:
        """测试计数器递增。"""
        m = MetricsCollector()
        m.incr("test_counter")
        m.incr("test_counter", value=2.5)
        snap = m.snapshot()
        assert snap["counters"]["test_counter"] == 3.5

    def test_gauge_set(self) -> None:
        """测试仪表盘设置。"""
        m = MetricsCollector()
        m.gauge("memory_usage", 75.5)
        m.gauge("memory_usage", 80.0)  # 覆盖
        snap = m.snapshot()
        assert snap["gauges"]["memory_usage"] == 80.0

    def test_observe_histogram(self) -> None:
        """测试直方图观测值记录与统计。"""
        m = MetricsCollector()
        values = [10.0, 20.0, 30.0, 40.0, 50.0]
        for v in values:
            m.observe("latency", v)
        snap = m.snapshot()
        stats = snap["histograms"]["latency"]
        assert stats["count"] == 5
        assert stats["mean"] == 30.0
        assert stats["min"] == 10.0
        assert stats["max"] == 50.0
        assert stats["p50"] == 30.0

    def test_observe_histogram_trimming(self) -> None:
        """测试直方图超过 10000 条时自动裁剪。"""
        m = MetricsCollector()
        for i in range(10001):
            m.observe("big_hist", float(i))
        assert len(m._histograms["big_hist"]) <= 5001  # 裁剪到最近 5000 条

    def test_snapshot_uptime(self) -> None:
        """测试快照包含运行时间。"""
        m = MetricsCollector()
        time.sleep(0.01)
        snap = m.snapshot()
        assert "uptime_seconds" in snap
        assert snap["uptime_seconds"] >= 0

    def test_snapshot_empty_histograms(self) -> None:
        """测试无观测值时直方图统计为空。"""
        m = MetricsCollector()
        snap = m.snapshot()
        assert snap["histograms"] == {}

    def test_to_prometheus_with_counters(self) -> None:
        """测试 Prometheus 格式输出包含计数器。"""
        m = MetricsCollector()
        m.incr("http_requests_total", 100)
        output = m.to_prometheus()
        assert "# TYPE http_requests_total counter" in output
        assert "http_requests_total 100.0" in output

    def test_to_prometheus_with_gauges(self) -> None:
        """测试 Prometheus 格式输出包含仪表盘。"""
        m = MetricsCollector()
        m.gauge("active_connections", 42)
        output = m.to_prometheus()
        assert "# TYPE active_connections gauge" in output
        assert "active_connections 42" in output

    def test_to_prometheus_with_histograms(self) -> None:
        """测试 Prometheus 格式输出包含直方图摘要。"""
        m = MetricsCollector()
        m.observe("api_latency", 0.1)
        m.observe("api_latency", 0.2)
        m.observe("api_latency", 0.3)
        output = m.to_prometheus()
        assert "# TYPE api_latency summary" in output
        assert "api_latency_count 3" in output
        assert 'api_latency{quantile="0.5"}' in output
        assert 'api_latency{quantile="0.95"}' in output
        assert 'api_latency{quantile="0.99"}' in output

    def test_to_prometheus_includes_uptime(self) -> None:
        """测试 Prometheus 输出始终包含运行时间。"""
        m = MetricsCollector()
        output = m.to_prometheus()
        assert "app_uptime_seconds" in output
        assert output.endswith("\n")

    def test_to_prometheus_empty(self) -> None:
        """测试无指标时 Prometheus 输出仅含 uptime。"""
        m = MetricsCollector()
        output = m.to_prometheus()
        lines = output.strip().split("\n")
        assert len(lines) == 2  # # TYPE + value


class TestGetMetricsSingleton:
    """测试全局指标收集器单例。"""

    def test_get_metrics_returns_same_instance(self) -> None:
        """测试 get_metrics 返回单例。"""
        m1 = get_metrics()
        m2 = get_metrics()
        assert m1 is m2

    def test_get_metrics_incr_persists(self) -> None:
        """测试通过单例递增的值在后续调用中保留。"""
        m = get_metrics()
        before = m.snapshot()["counters"].get("singleton_test", 0)
        m.incr("singleton_test")
        after = get_metrics().snapshot()["counters"].get("singleton_test", 0)
        assert after == before + 1
