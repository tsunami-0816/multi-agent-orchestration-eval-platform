"""db_query 工具（T3-7）。

只读 SQL 查询连接业务 SQLite 库：
- 仅允许 SELECT，禁止 INSERT/UPDATE/DELETE/DROP
- 查询超时 5s
- 结果行数上限 1000

架构决策：提供只读数据库查询能力，便于 Agent 分析业务数据，
同时通过 SQL 类型白名单和超时限制保证安全。
"""

from __future__ import annotations

import re
from typing import Any

from app.config import get_config
from app.core.exceptions import AppException, ErrorCode
from app.core.logging import get_logger

logger = get_logger(__name__)

# 禁止的 SQL 关键字（不区分大小写）
_FORBIDDEN_KEYWORDS = [
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE",
    "TRUNCATE", "REPLACE", "PRAGMA", "ATTACH", "DETACH",
    "VACUUM", "REINDEX", "GRANT", "REVOKE", "BEGIN", "COMMIT",
    "ROLLBACK", "SAVEPOINT", "RELEASE",
]

# 编译禁止关键字正则（匹配独立单词）
_forbidden_pattern = re.compile(
    r"\b(" + "|".join(_FORBIDDEN_KEYWORDS) + r")\b",
    re.IGNORECASE,
)


class DBQueryTool:
    """db_query 工具适配器（T3-7）。

    对业务 SQLite 数据库执行只读 SELECT 查询。
    """

    name = "db_query"
    description = (
        "对业务数据库执行只读 SQL 查询。仅支持 SELECT 语句，"
        "禁止 INSERT/UPDATE/DELETE/DROP 等写操作。"
        "查询超时 5 秒，结果最多返回 1000 行。"
        "可用于查询任务状态、评测结果、LLM 用量等业务数据。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "sql": {
                "type": "string",
                "description": "要执行的 SELECT SQL 语句",
                "minLength": 1,
                "maxLength": 5000,
            },
            "limit": {
                "type": "integer",
                "description": "返回行数上限（默认100，最大1000）",
                "minimum": 1,
                "maximum": 1000,
                "default": 100,
            },
        },
        "required": ["sql"],
    }

    def __init__(self) -> None:
        """初始化工具，读取配置。"""
        config = get_config()
        self.database_url = config.database_url
        self.max_rows = getattr(config, "mcp_max_query_rows", 1000)
        self.timeout = 5  # 查询超时 5 秒

    def execute(self, arguments: dict[str, Any]) -> str:
        """执行只读 SQL 查询。

        Args:
            arguments: 参数字典，含 sql（必填）、limit（可选）

        Returns:
            查询结果文本（表格格式）

        Raises:
            AppException(TOOL_VALIDATION_ERROR): SQL 包含写操作时抛出
            AppException(TOOL_TIMEOUT): 查询超时时抛出
        """
        sql = arguments["sql"].strip()
        limit = min(arguments.get("limit", 100), self.max_rows)

        # 校验：必须以 SELECT 开头
        if not sql.upper().lstrip().startswith("SELECT"):
            raise AppException(
                ErrorCode.TOOL_VALIDATION_ERROR,
                "仅支持 SELECT 查询语句",
                details={"sql": sql[:200]},
            )

        # 校验：禁止写操作关键字
        forbidden = _forbidden_pattern.findall(sql)
        if forbidden:
            unique_keywords = list(set(k.upper() for k in forbidden))
            logger.warning("db_query_forbidden_keywords", keywords=unique_keywords)
            raise AppException(
                ErrorCode.TOOL_VALIDATION_ERROR,
                f"SQL 包含禁止的写操作关键字: {', '.join(unique_keywords)}",
                details={"forbidden": unique_keywords, "sql": sql[:200]},
            )

        # 自动添加 LIMIT（如果用户未指定）
        if "LIMIT" not in sql.upper():
            sql = f"{sql} LIMIT {limit}"

        # 执行查询（同步执行，因为 MCP stdio 是同步协议）
        try:
            import sqlite3

            # 从 database_url 提取文件路径
            db_path = self._extract_db_path(self.database_url)

            conn = sqlite3.connect(db_path, timeout=self.timeout)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(sql)
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            conn.close()

            logger.info("db_query_success", columns=len(columns), rows=len(rows))
            return self._format_results(columns, rows, limit)

        except sqlite3.OperationalError as e:
            if "timeout" in str(e).lower() or "locked" in str(e).lower():
                logger.error("db_query_timeout", error=str(e))
                raise AppException(ErrorCode.TOOL_TIMEOUT, f"数据库查询超时或锁定: {e}") from e
            return f"[SQL 错误] {e}"
        except Exception as e:
            logger.error("db_query_failed", error=str(e))
            return f"[查询错误] {e}"

    @staticmethod
    def _extract_db_path(database_url: str) -> str:
        """从 SQLAlchemy URL 中提取 SQLite 文件路径。

        Args:
            database_url: SQLAlchemy 连接 URL

        Returns:
            SQLite 文件路径
        """
        # 格式：sqlite+aiosqlite:///relative/path.db
        # 或：sqlite+aiosqlite:////absolute/path.db
        prefix = "sqlite+aiosqlite:///"
        if database_url.startswith(prefix):
            path = database_url[len(prefix):]
            # 处理三斜杠（相对路径）和四斜杠（绝对路径）
            if path.startswith("/"):
                return path  # 绝对路径
            return path  # 相对路径
        return database_url

    @staticmethod
    def _format_results(columns: list[str], rows: list, limit: int) -> str:
        """格式化查询结果为文本表格。

        Args:
            columns: 列名列表
            rows: 行数据列表
            limit: 行数限制

        Returns:
            格式化的文本结果
        """
        if not columns:
            return "(查询无返回列)"

        if not rows:
            return "(查询结果为空)"

        # 限制显示行数
        display_rows = rows[:limit]
        truncated = len(rows) > limit

        # 计算每列宽度
        col_widths = [len(col) for col in columns]
        for row in display_rows:
            for i, col in enumerate(columns):
                val = str(row[i]) if row[i] is not None else "NULL"
                col_widths[i] = max(col_widths[i], min(len(val), 50))

        # 构建表格
        lines = []
        header = " | ".join(col.ljust(col_widths[i]) for i, col in enumerate(columns))
        separator = "-+-".join("-" * w for w in col_widths)
        lines.append(header)
        lines.append(separator)

        for row in display_rows:
            values = []
            for i, col in enumerate(columns):
                val = str(row[i]) if row[i] is not None else "NULL"
                if len(val) > 50:
                    val = val[:47] + "..."
                values.append(val.ljust(col_widths[i]))
            lines.append(" | ".join(values))

        if truncated:
            lines.append(f"\n... 仅显示前 {limit} 行，共 {len(rows)} 行")

        lines.append(f"\n共 {len(rows)} 行，{len(columns)} 列")
        return "\n".join(lines)
