"""报告生成模块（M7）。

提供评测报告生成能力：
- ReportAggregator: 聚合统计
- ReportGenerator: HTML/JSON 报告生成
- ReportStorage: 文件存储
"""

from app.report.generator import ReportGenerator
from app.report.storage import ReportStorage

__all__ = ["ReportGenerator", "ReportStorage"]
