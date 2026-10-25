"""code_exec 工具（T3-5，P1-8 修正）。

在临时目录执行 Python 代码，安全隔离：
- 临时目录 /tmp/code_exec_{uuid}/（Windows 下 %TEMP%/code_exec_{uuid}/）
- 执行超时 10s
- 危险操作过滤（禁止 os.remove/shutil.rmtree/环境变量/socket 网络）
- 执行后清理临时目录

架构决策（P1-8/R-13）：声明为"可信本地环境"模式，
通过临时目录+超时+危险操作过滤实现基础安全隔离。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

from app.config import get_config
from app.core.exceptions import AppException, ErrorCode
from app.core.logging import get_logger

logger = get_logger(__name__)

# 危险操作模式列表（P1-8）
# 新增危险模式时及时补充
_DANGEROUS_PATTERNS = [
    # 文件删除
    r"\bos\.remove\b",
    r"\bos\.unlink\b",
    r"\bos\.rmdir\b",
    r"\bshutil\.rmtree\b",
    r"\bPath\.unlink\b",
    # 环境变量
    r"\bos\.environ\b",
    r"\bos\.getenv\b",
    r"\bos\.putenv\b",
    # 网络操作
    r"\bsocket\b",
    r"\burllib\b",
    r"\brequests\b",
    r"\bhttpx\b",
    # 进程操作
    r"\bos\.system\b",
    r"\bos\.popen\b",
    r"\bexec\b",
    r"\beval\b",
    # 敏感路径（简单字符串匹配）
    r"/etc/passwd",
    r"/etc/shadow",
    r"/root/",
    # 导入危险模块
    r"^\s*import\s+(socket|requests|httpx|urllib)",
    r"^\s*from\s+(socket|requests|httpx|urllib)",
]

# 编译正则
_compiled_patterns = [re.compile(p, re.MULTILINE) for p in _DANGEROUS_PATTERNS]


class CodeExecTool:
    """code_exec 工具适配器（T3-5，P1-8）。

    在隔离的临时目录中执行 Python 代码，返回 stdout。
    """

    name = "code_exec"
    description = (
        "执行 Python 代码并返回输出。适用于数据处理、计算、算法验证等场景。"
        "代码在临时目录中执行，有超时限制和安全过滤。"
        "禁止文件删除、网络请求、环境变量读取等危险操作。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "要执行的 Python 代码",
                "minLength": 1,
                "maxLength": 10000,
            },
            "timeout": {
                "type": "integer",
                "description": "执行超时秒数（默认10，最大30）",
                "minimum": 1,
                "maximum": 30,
                "default": 10,
            },
        },
        "required": ["code"],
    }

    def __init__(self) -> None:
        """初始化工具，读取配置。"""
        config = get_config()
        self.default_timeout = getattr(config, "mcp_code_exec_timeout", 10)

    def execute(self, arguments: dict[str, Any]) -> str:
        """执行 Python 代码。

        Args:
            arguments: 参数字典，含 code（必填）、timeout（可选）

        Returns:
            代码执行的 stdout 输出

        Raises:
            AppException(TOOL_VALIDATION_ERROR): 代码包含危险操作时抛出
            AppException(TOOL_TIMEOUT): 执行超时时抛出
        """
        code = arguments["code"]
        timeout = min(arguments.get("timeout", self.default_timeout), 30)

        # 危险操作过滤（P1-8）
        violations = self._check_dangerous(code)
        if violations:
            logger.warning("code_exec_blocked", violations=violations)
            raise AppException(
                ErrorCode.TOOL_VALIDATION_ERROR,
                "代码包含被禁止的危险操作",
                details={"violations": violations, "code_preview": code[:200]},
            )

        # 创建临时目录
        work_dir = Path(tempfile.gettempdir()) / f"code_exec_{uuid.uuid4().hex[:8]}"
        work_dir.mkdir(parents=True, exist_ok=True)

        code_file = work_dir / "main.py"
        code_file.write_text(code, encoding="utf-8")

        try:
            # 在子进程中执行代码
            result = subprocess.run(
                [sys.executable, str(code_file)],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(work_dir),
                env={"PATH": os.environ.get("PATH", ""), "PYTHONIOENCODING": "utf-8"},
            )

            output = result.stdout
            if result.stderr:
                output += f"\n[stderr]\n{result.stderr}"
            if result.returncode != 0:
                output += f"\n[进程退出码: {result.returncode}]"

            logger.info("code_exec_success", duration=timeout, returncode=result.returncode)
            return output if output.strip() else "(代码执行完成，无输出)"

        except subprocess.TimeoutExpired:
            logger.error("code_exec_timeout", timeout=timeout)
            raise AppException(
                ErrorCode.TOOL_TIMEOUT,
                f"代码执行超时（{timeout}秒），进程已被终止",
            )
        except Exception as e:
            logger.error("code_exec_failed", error=str(e))
            return f"[执行错误] {e}"
        finally:
            # 清理临时目录（P1-8）
            try:
                shutil.rmtree(work_dir, ignore_errors=True)
            except Exception:
                pass

    @staticmethod
    def _check_dangerous(code: str) -> list[str]:
        """检查代码中是否包含危险操作（P1-8）。

        Args:
            code: 待检查的代码

        Returns:
            违规模式列表，空列表表示通过
        """
        violations: list[str] = []
        for pattern in _compiled_patterns:
            matches = pattern.findall(code)
            if matches:
                # 取匹配到的文本作为违规描述
                for m in pattern.finditer(code):
                    matched_text = m.group(0).strip()
                    if matched_text and matched_text not in violations:
                        violations.append(matched_text)
                        if len(violations) >= 5:  # 最多返回5条
                            return violations
        return violations
