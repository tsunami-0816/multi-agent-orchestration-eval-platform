"""file_rw 工具（T3-6）。

限定工作目录 data/workspace/ 内文件读写：
- 路径白名单校验，禁止 .. 路径穿越
- 单文件 ≤1MB
- 支持读取、写入、追加、列表、删除操作

架构决策（安全规范）：文件操作必须校验路径在允许目录内，
使用 resolve().is_relative_to() 防止路径穿越攻击。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config import get_config, get_config_paths
from app.core.exceptions import AppException, ErrorCode
from app.core.logging import get_logger

logger = get_logger(__name__)

# 允许的操作类型
_ALLOWED_OPERATIONS = {"read", "write", "append", "list", "delete", "exists"}


class FileRWTool:
    """file_rw 工具适配器（T3-6）。

    在限定工作目录内进行文件读写操作，防止路径穿越。
    """

    name = "file_rw"
    description = (
        "在工作目录中读写文件。支持读取、写入、追加、列表、删除、存在检查。"
        "所有操作限制在工作目录内，禁止路径穿越。单文件大小上限 1MB。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "description": "操作类型：read/write/append/list/delete/exists",
                "enum": ["read", "write", "append", "list", "delete", "exists"],
            },
            "path": {
                "type": "string",
                "description": "相对于工作目录的文件路径",
                "minLength": 1,
                "maxLength": 512,
            },
            "content": {
                "type": "string",
                "description": "写入/追加的内容（write/append 操作必填）",
            },
        },
        "required": ["operation", "path"],
    }

    def __init__(self) -> None:
        """初始化工具，获取工作目录。"""
        config = get_config()
        paths = get_config_paths()
        self.workspace_dir = paths.get("workspace", Path(config.workspace_dir)).resolve()
        self.max_file_size = getattr(config, "mcp_max_file_size", 1048576)
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

    def execute(self, arguments: dict[str, Any]) -> str:
        """执行文件操作。

        Args:
            arguments: 参数字典，含 operation/path（必填）、content（write/append 必填）

        Returns:
            操作结果文本

        Raises:
            AppException(TOOL_VALIDATION_ERROR): 路径穿越/文件超限/参数缺失时抛出
        """
        operation = arguments["operation"]
        relative_path = arguments["path"]

        # 安全校验：解析绝对路径并确认在工作目录内
        safe_path = self._resolve_safe_path(relative_path)

        if operation == "read":
            return self._read(safe_path)
        elif operation == "write":
            content = arguments.get("content", "")
            return self._write(safe_path, content, mode="w")
        elif operation == "append":
            content = arguments.get("content", "")
            return self._write(safe_path, content, mode="a")
        elif operation == "list":
            return self._list(safe_path)
        elif operation == "delete":
            return self._delete(safe_path)
        elif operation == "exists":
            return self._exists(safe_path)
        else:
            raise AppException(
                ErrorCode.TOOL_VALIDATION_ERROR,
                f"不支持的操作类型: {operation}",
                details={"allowed": list(_ALLOWED_OPERATIONS)},
            )

    def _resolve_safe_path(self, relative_path: str) -> Path:
        """解析并校验路径安全性（防止路径穿越）。

        Args:
            relative_path: 相对于工作目录的路径

        Returns:
            安全的绝对路径

        Raises:
            AppException(TOOL_VALIDATION_ERROR): 路径穿越时抛出
        """
        # 禁止绝对路径
        if Path(relative_path).is_absolute():
            raise AppException(
                ErrorCode.TOOL_VALIDATION_ERROR,
                "禁止使用绝对路径，请使用相对于工作目录的路径",
                details={"path": relative_path},
            )

        # 解析绝对路径
        absolute_path = (self.workspace_dir / relative_path).resolve()

        # 校验在工作目录内（防止 .. 穿越）
        try:
            absolute_path.relative_to(self.workspace_dir)
        except ValueError:
            logger.warning("file_rw_path_traversal_blocked", path=relative_path)
            raise AppException(
                ErrorCode.TOOL_VALIDATION_ERROR,
                "路径穿越被拒绝：文件必须在工作目录内",
                details={"path": relative_path, "workspace": str(self.workspace_dir)},
            )

        return absolute_path

    def _read(self, file_path: Path) -> str:
        """读取文件内容。

        Args:
            file_path: 文件绝对路径

        Returns:
            文件内容文本
        """
        if not file_path.exists():
            return f"[错误] 文件不存在: {file_path.name}"
        if not file_path.is_file():
            return f"[错误] 路径不是文件: {file_path.name}"

        # 检查文件大小
        if file_path.stat().st_size > self.max_file_size:
            return f"[错误] 文件大小超过限制（{self.max_file_size // 1024}KB）"

        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
            logger.info("file_rw_read", path=file_path.name, size=len(content))
            return content
        except Exception as e:
            return f"[读取错误] {e}"

    def _write(self, file_path: Path, content: str, mode: str = "w") -> str:
        """写入或追加文件内容。

        Args:
            file_path: 文件绝对路径
            content: 要写入的内容
            mode: 写入模式（w=覆盖, a=追加）

        Returns:
            操作结果文本
        """
        # 检查内容大小
        if len(content.encode("utf-8")) > self.max_file_size:
            return f"[错误] 内容大小超过限制（{self.max_file_size // 1024}KB）"

        try:
            # 确保父目录存在
            file_path.parent.mkdir(parents=True, exist_ok=True)

            with file_path.open(mode, encoding="utf-8") as f:
                f.write(content)

            action = "写入" if mode == "w" else "追加"
            logger.info(f"file_rw_{'write' if mode == 'w' else 'append'}", path=file_path.name, size=len(content))
            return f"[成功] {action}文件: {file_path.name}（{len(content)} 字符）"
        except Exception as e:
            return f"[写入错误] {e}"

    def _list(self, dir_path: Path) -> str:
        """列出目录内容。

        Args:
            dir_path: 目录绝对路径

        Returns:
            目录内容列表文本
        """
        if not dir_path.exists():
            return f"[错误] 目录不存在: {dir_path.name}"
        if not dir_path.is_dir():
            return f"[错误] 路径不是目录: {dir_path.name}"

        try:
            items = sorted(dir_path.iterdir())
            if not items:
                return "(目录为空)"

            lines = [f"目录内容（{len(items)} 项）：", ""]
            for item in items:
                item_type = "[DIR]" if item.is_dir() else "[FILE]"
                size = f" ({item.stat().st_size} bytes)" if item.is_file() else ""
                lines.append(f"  {item_type} {item.name}{size}")
            return "\n".join(lines)
        except Exception as e:
            return f"[列表错误] {e}"

    def _delete(self, file_path: Path) -> str:
        """删除文件（仅允许删除工作目录内的文件）。

        Args:
            file_path: 文件绝对路径

        Returns:
            操作结果文本
        """
        if not file_path.exists():
            return f"[错误] 文件不存在: {file_path.name}"
        if file_path.is_dir():
            return f"[错误] 不能删除目录，请使用文件路径: {file_path.name}"

        try:
            file_path.unlink()
            logger.info("file_rw_delete", path=file_path.name)
            return f"[成功] 已删除文件: {file_path.name}"
        except Exception as e:
            return f"[删除错误] {e}"

    def _exists(self, file_path: Path) -> str:
        """检查文件/目录是否存在。

        Args:
            file_path: 路径

        Returns:
            存在性检查结果
        """
        exists = file_path.exists()
        item_type = "目录" if file_path.is_dir() else "文件" if file_path.is_file() else "路径"
        return f"{'存在' if exists else '不存在'}: {file_path.name}（{item_type}）"
