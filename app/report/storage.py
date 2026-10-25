"""报告文件存储（T7-4）。

管理报告文件的存储和读取：
- HTML 报告存储
- JSON 报告存储
- 报告文件查询
- 报告清理

存储路径：data/reports/{run_id}/report.html + report.json

架构决策（T-01）：报告文件存储在本地文件系统，
数据库仅保存元数据和路径，避免大字段影响数据库性能。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from app.config import get_config, get_config_paths
from app.core.logging import get_logger

logger = get_logger(__name__)

# 默认报告存储目录
DEFAULT_REPORTS_DIR = Path("data/reports")


class ReportStorage:
    """报告文件存储（T7-4）。

    管理报告文件的读写。
    """

    def __init__(self, reports_dir: Optional[Path] = None) -> None:
        """初始化存储。

        Args:
            reports_dir: 报告存储目录，为 None 时使用默认路径
        """
        paths = get_config_paths()
        self.reports_dir = reports_dir or paths.get("reports", DEFAULT_REPORTS_DIR)
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def _get_run_dir(self, run_id: str) -> Path:
        """获取运行的报告目录。

        Args:
            run_id: 运行 ID

        Returns:
            报告目录路径
        """
        run_dir = self.reports_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    def save_html(self, run_id: str, content: str) -> Path:
        """保存 HTML 报告。

        Args:
            run_id: 运行 ID
            content: HTML 内容

        Returns:
            保存的文件路径
        """
        file_path = self._get_run_dir(run_id) / "report.html"
        file_path.write_text(content, encoding="utf-8")
        logger.info("report_html_saved", run_id=run_id, path=str(file_path))
        return file_path

    def save_json(self, run_id: str, content: str) -> Path:
        """保存 JSON 报告。

        Args:
            run_id: 运行 ID
            content: JSON 字符串

        Returns:
            保存的文件路径
        """
        file_path = self._get_run_dir(run_id) / "report.json"
        file_path.write_text(content, encoding="utf-8")
        logger.info("report_json_saved", run_id=run_id, path=str(file_path))
        return file_path

    def get_html_path(self, run_id: str) -> Optional[Path]:
        """获取 HTML 报告路径。

        Args:
            run_id: 运行 ID

        Returns:
            文件路径，不存在时返回 None
        """
        file_path = self._get_run_dir(run_id) / "report.html"
        return file_path if file_path.exists() else None

    def get_json_path(self, run_id: str) -> Optional[Path]:
        """获取 JSON 报告路径。

        Args:
            run_id: 运行 ID

        Returns:
            文件路径，不存在时返回 None
        """
        file_path = self._get_run_dir(run_id) / "report.json"
        return file_path if file_path.exists() else None

    def read_json(self, run_id: str) -> Optional[str]:
        """读取 JSON 报告内容。

        Args:
            run_id: 运行 ID

        Returns:
            JSON 字符串，不存在时返回 None
        """
        file_path = self.get_json_path(run_id)
        if file_path:
            return file_path.read_text(encoding="utf-8")
        return None

    def delete_report(self, run_id: str) -> bool:
        """删除运行的全部报告文件。

        Args:
            run_id: 运行 ID

        Returns:
            True 表示删除成功
        """
        import shutil

        run_dir = self.reports_dir / run_id
        if run_dir.exists():
            shutil.rmtree(run_dir, ignore_errors=True)
            logger.info("report_deleted", run_id=run_id)
            return True
        return False

    def list_reports(self) -> list[str]:
        """列出全部有报告的运行 ID。

        Returns:
            运行 ID 列表
        """
        if not self.reports_dir.exists():
            return []
        return [d.name for d in self.reports_dir.iterdir() if d.is_dir()]
