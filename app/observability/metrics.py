"""指标收集（T11-1/T11-2）。

收集 7 项核心指标（T-05）：
1. 任务总数/成功率/平均耗时
2. LLM 调用次数/Token 用量/费用
3. 工具调用次数/成功率
4. 评测运行数/平均分/通过率
5. API 请求数/错误率/P99 延迟
6. 数据库连接数/查询耗时
7. 系统资源（CPU/内存）

提供 Prometheus 格式的 /metrics 端点。
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


class MetricsCollector:
    """指标收集器（T11-1）。

    内存中的指标计数器，支持递增、计时、快照。
    """

    def __init__(self) -> None:
        """初始化指标收集器。"""
        self._counters: dict[str, float] = defaultdict(float)
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, list[float]] = defaultdict(list)
        self._start_time = time.time()

    def incr(self, name: str, value: float = 1.0) -> None:
        """递增计数器。

        Args:
            name: 指标名称
            value: 递增值
        """
        self._counters[name] += value

    def gauge(self, name: str, value: float) -> None:
        """设置仪表盘值。

        Args:
            name: 指标名称
            value: 当前值
        """
        self._gauges[name] = value

    def observe(self, name: str, value: float) -> None:
        """记录观测值（用于直方图/延迟统计）。

        Args:
            name: 指标名称
            value: 观测值
        """
        self._histograms[name].append(value)
        # 限制历史数据量
        if len(self._histograms[name]) > 10000:
            self._histograms[name] = self._histograms[name][-5000:]

    def snapshot(self) -> dict[str, Any]:
        """获取指标快照。

        Returns:
            完整指标字典
        """
        uptime = time.time() - self._start_time

        # 计算直方图统计
        histogram_stats = {}
        for name, values in self._histograms.items():
            if values:
                sorted_vals = sorted(values)
                histogram_stats[name] = {
                    "count": len(values),
                    "mean": sum(values) / len(values),
                    "p50": sorted_vals[len(sorted_vals) // 2],
                    "p95": sorted_vals[int(len(sorted_vals) * 0.95)],
                    "p99": sorted_vals[int(len(sorted_vals) * 0.99)],
                    "min": min(values),
                    "max": max(values),
                }

        return {
            "uptime_seconds": round(uptime, 2),
            "counters": dict(self._counters),
            "gauges": dict(self._gauges),
            "histograms": histogram_stats,
        }

    def to_prometheus(self) -> str:
        """转换为 Prometheus 文本格式（T11-2）。

        Returns:
            Prometheus 格式的指标文本
        """
        lines = []
        snapshot = self.snapshot()

        # 计数器
        for name, value in snapshot["counters"].items():
            lines.append(f"# TYPE {name} counter")
            lines.append(f"{name} {value}")

        # 仪表盘
        for name, value in snapshot["gauges"].items():
            lines.append(f"# TYPE {name} gauge")
            lines.append(f"{name} {value}")

        # 直方图
        for name, stats in snapshot["histograms"].items():
            lines.append(f"# TYPE {name} summary")
            lines.append(f"{name}_count {stats['count']}")
            lines.append(f"{name}_sum {stats['mean'] * stats['count']}")
            lines.append(f'{name}{{quantile="0.5"}} {stats["p50"]}')
            lines.append(f'{name}{{quantile="0.95"}} {stats["p95"]}')
            lines.append(f'{name}{{quantile="0.99"}} {stats["p99"]}')

        # 运行时间
        lines.append("# TYPE app_uptime_seconds gauge")
        lines.append(f"app_uptime_seconds {snapshot['uptime_seconds']}")

        return "\n".join(lines) + "\n"


# 全局指标收集器单例
_metrics: MetricsCollector | None = None


def get_metrics() -> MetricsCollector:
    """获取全局指标收集器。

    Returns:
        MetricsCollector 实例
    """
    global _metrics
    if _metrics is None:
        _metrics = MetricsCollector()
    return _metrics
