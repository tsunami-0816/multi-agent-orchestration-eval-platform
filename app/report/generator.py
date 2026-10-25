"""报告生成器（T7-2/T7-3）。

生成评测报告：
- HTML 报告：可视化、可交互的网页报告
- JSON 报告：结构化数据，便于程序处理
- 报告内容：总体统计、维度分析、分类统计、用例详情

架构决策（T-01）：使用字符串模板生成 HTML，
不依赖额外模板引擎，确保轻量和可移植。
JSON 报告直接序列化聚合统计结果。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.models.eval import EvalRun, EvalScore
from app.report.aggregator import ReportAggregator
from app.report.storage import ReportStorage

logger = get_logger(__name__)


class ReportGenerator:
    """报告生成器（T7-2/T7-3）。

    生成 HTML 和 JSON 格式的评测报告。
    """

    def __init__(self) -> None:
        """初始化生成器。"""
        self.aggregator = ReportAggregator()
        self.storage = ReportStorage()

    async def generate_report(
        self,
        run: EvalRun,
        scores: list[EvalScore],
    ) -> dict[str, Any]:
        """生成完整报告（HTML + JSON）。

        Args:
            run: 评测运行
            scores: 评分记录列表

        Returns:
            报告信息字典，含 html_path/json_path/report_id
        """
        run_info = {
            "run_id": run.id,
            "name": run.name,
            "model_config": run.model_config,
            "total_cases": run.total_cases,
            "completed_cases": run.completed_cases,
            "passed_cases": run.passed_cases,
            "avg_score": run.avg_score,
            "status": run.status,
            "created_at": run.created_at.isoformat() if run.created_at else "",
            "completed_at": run.completed_at.isoformat() if run.completed_at else "",
        }

        # 聚合统计
        report_data = self.aggregator.aggregate(scores, run_info)

        # 生成 JSON 报告
        json_content = json.dumps(report_data, ensure_ascii=False, indent=2)
        json_path = self.storage.save_json(run.id, json_content)

        # 生成 HTML 报告
        html_content = self._render_html(report_data)
        html_path = self.storage.save_html(run.id, html_content)

        # 保存报告记录到数据库
        await self._save_report_record(run.id, report_data, str(html_path), str(json_path))

        logger.info("report_generated", run_id=run.id, html_path=str(html_path), json_path=str(json_path))

        return {
            "report_id": run.id,
            "html_path": str(html_path),
            "json_path": str(json_path),
        }

    def _render_html(self, data: dict[str, Any]) -> str:
        """渲染 HTML 报告（T7-2）。

        Args:
            data: 聚合统计数据

        Returns:
            HTML 字符串
        """
        run_info = data.get("run_info", {})
        summary = data.get("summary", {})
        dimensions = data.get("dimension_stats", {})
        by_category = data.get("by_category", {})
        top_cases = data.get("top_cases", [])
        bottom_cases = data.get("bottom_cases", [])
        distribution = data.get("score_distribution", {})

        # 通过率颜色
        pass_rate = summary.get("pass_rate", 0)
        pass_rate_color = "#22c55e" if pass_rate >= 0.8 else "#eab308" if pass_rate >= 0.6 else "#ef4444"
        avg_score = summary.get("mean", 0)
        avg_color = "#22c55e" if avg_score >= 8 else "#eab308" if avg_score >= 6 else "#ef4444"

        # 分类统计行
        category_rows = ""
        for cat, stats in by_category.items():
            category_rows += f"""
            <tr>
                <td>{cat}</td>
                <td>{stats.get('count', 0)}</td>
                <td>{stats.get('mean', 0)}</td>
                <td>{stats.get('pass_rate', 0):.1%}</td>
            </tr>"""

        # 最高分用例行
        top_rows = ""
        for i, case in enumerate(top_cases, 1):
            top_rows += f"""
            <tr>
                <td>{i}</td>
                <td>{case['case_id']}</td>
                <td>{case['score']}</td>
                <td>{case.get('feedback', '')[:50]}</td>
            </tr>"""

        # 最低分用例行
        bottom_rows = ""
        for i, case in enumerate(bottom_cases, 1):
            bottom_rows += f"""
            <tr>
                <td>{i}</td>
                <td>{case['case_id']}</td>
                <td>{case['score']}</td>
                <td>{case.get('feedback', '')[:50]}</td>
            </tr>"""

        # 分数分布条
        dist_bars = ""
        max_count = max(distribution.values()) if distribution else 1
        for range_label, count in distribution.items():
            width = (count / max_count * 100) if max_count > 0 else 0
            dist_bars += f"""
            <div style="margin-bottom:8px">
                <span style="display:inline-block;width:50px">{range_label}</span>
                <div style="display:inline-block;width:200px;background:#e5e7eb;border-radius:4px">
                    <div style="width:{width}%;background:#3b82f6;height:20px;border-radius:4px"></div>
                </div>
                <span style="margin-left:8px">{count}</span>
            </div>"""

        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>评测报告 - {run_info.get('name', '')}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 0; padding: 20px; background: #f8fafc; color: #1e293b; }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
        .header {{ background: white; border-radius: 8px; padding: 24px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
        .header h1 {{ margin: 0 0 8px 0; font-size: 24px; }}
        .header .meta {{ color: #64748b; font-size: 14px; }}
        .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 20px; }}
        .card {{ background: white; border-radius: 8px; padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
        .card .label {{ color: #64748b; font-size: 14px; margin-bottom: 4px; }}
        .card .value {{ font-size: 28px; font-weight: bold; }}
        .section {{ background: white; border-radius: 8px; padding: 24px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
        .section h2 {{ margin: 0 0 16px 0; font-size: 18px; border-bottom: 2px solid #e2e8f0; padding-bottom: 8px; }}
        table {{ width: 100%; border-collapse: collapse; }}
        th, td {{ padding: 10px 12px; text-align: left; border-bottom: 1px solid #e2e8f0; }}
        th {{ background: #f1f5f9; font-weight: 600; }}
        .dimensions {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }}
        .dim-item {{ text-align: center; padding: 16px; background: #f8fafc; border-radius: 6px; }}
        .dim-item .dim-label {{ color: #64748b; font-size: 13px; }}
        .dim-item .dim-value {{ font-size: 24px; font-weight: bold; color: #3b82f6; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>评测报告：{run_info.get('name', '')}</h1>
            <div class="meta">
                运行 ID: {run_info.get('run_id', '')} |
                模型: {run_info.get('model_config', '')} |
                完成时间: {run_info.get('completed_at', '')}
            </div>
        </div>

        <div class="cards">
            <div class="card">
                <div class="label">平均分</div>
                <div class="value" style="color:{avg_color}">{avg_score}</div>
            </div>
            <div class="card">
                <div class="label">通过率</div>
                <div class="value" style="color:{pass_rate_color}">{pass_rate:.1%}</div>
            </div>
            <div class="card">
                <div class="label">用例总数</div>
                <div class="value">{summary.get('count', 0)}</div>
            </div>
            <div class="card">
                <div class="label">通过/失败</div>
                <div class="value" style="font-size:20px">{summary.get('passed_count', 0)} / {summary.get('failed_count', 0)}</div>
            </div>
        </div>

        <div class="section">
            <h2>维度分析</h2>
            <div class="dimensions">
                <div class="dim-item">
                    <div class="dim-label">准确性</div>
                    <div class="dim-value">{dimensions.get('accuracy', 0)}</div>
                </div>
                <div class="dim-item">
                    <div class="dim-label">相关性</div>
                    <div class="dim-value">{dimensions.get('relevance', 0)}</div>
                </div>
                <div class="dim-item">
                    <div class="dim-label">完整性</div>
                    <div class="dim-value">{dimensions.get('completeness', 0)}</div>
                </div>
                <div class="dim-item">
                    <div class="dim-label">格式</div>
                    <div class="dim-value">{dimensions.get('format', 0)}</div>
                </div>
            </div>
        </div>

        <div class="section">
            <h2>分数分布</h2>
            {dist_bars}
        </div>

        <div class="section">
            <h2>分类统计</h2>
            <table>
                <thead><tr><th>类别</th><th>用例数</th><th>平均分</th><th>通过率</th></tr></thead>
                <tbody>{category_rows}</tbody>
            </table>
        </div>

        <div class="section">
            <h2>最高分用例 Top 5</h2>
            <table>
                <thead><tr><th>#</th><th>用例 ID</th><th>分数</th><th>反馈</th></tr></thead>
                <tbody>{top_rows}</tbody>
            </table>
        </div>

        <div class="section">
            <h2>最低分用例 Top 5</h2>
            <table>
                <thead><tr><th>#</th><th>用例 ID</th><th>分数</th><th>反馈</th></tr></thead>
                <tbody>{bottom_rows}</tbody>
            </table>
        </div>
    </div>
</body>
</html>"""

    async def _save_report_record(
        self,
        run_id: str,
        report_data: dict[str, Any],
        html_path: str,
        json_path: str,
    ) -> None:
        """保存报告记录到数据库。

        修复-BUG-010: 修正EvalReport字段名（run_id/report_type/file_path/file_size_bytes）
        修复-NEW-004: 创建html和json两条记录，各对应一个文件路径和大小

        Args:
            run_id: 运行 ID
            report_data: 报告数据
            html_path: HTML 文件路径
            json_path: JSON 文件路径
        """
        try:
            import os
            from app.models.base import async_session_factory
            from app.models.eval import EvalReport
            from app.repositories.eval_repo import EvalRepository

            async with async_session_factory() as db:
                repo = EvalRepository(db)

                # 修复-BUG-010/NEW-004 创建HTML报告记录
                html_size = os.path.getsize(html_path) if os.path.exists(html_path) else 0
                html_report = EvalReport(
                    run_id=run_id,
                    report_type="html",
                    file_path=html_path,
                    file_size_bytes=html_size,
                )
                await repo.insert_report(html_report)

                # 修复-BUG-010/NEW-004 创建JSON报告记录
                json_size = os.path.getsize(json_path) if os.path.exists(json_path) else 0
                json_report = EvalReport(
                    run_id=run_id,
                    report_type="json",
                    file_path=json_path,
                    file_size_bytes=json_size,
                )
                await repo.insert_report(json_report)

                await repo.commit()
                logger.info("report_records_saved", run_id=run_id, html_size=html_size, json_size=json_size)
        except Exception as e:
            logger.error("save_report_record_failed", run_id=run_id, error=str(e))
