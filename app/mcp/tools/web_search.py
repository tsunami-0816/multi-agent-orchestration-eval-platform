"""web_search 工具（T3-4，P1-7 修正）。

基于 DuckDuckGo HTML 抓取 + BeautifulSoup 解析，免费无 API Key。
- 返回 Top5 标题/链接/摘要
- 请求超时 10s，User-Agent 伪装
- 结果缓存 5 分钟（内存缓存）

架构决策（P1-7）：不依赖付费 SaaS（如 SerpAPI/Bing API），
使用 DuckDuckGo HTML 页面抓取，完全免费且无需 API Key。
"""

from __future__ import annotations

import time
import urllib.parse
import urllib.request
from typing import Any

from bs4 import BeautifulSoup

from app.config import get_config
from app.core.exceptions import AppException, ErrorCode
from app.core.logging import get_logger

logger = get_logger(__name__)

# 内存缓存：query -> (timestamp, results)
_cache: dict[str, tuple[float, list[dict[str, str]]]] = {}
_CACHE_TTL = 300  # 5 分钟

# 伪装 User-Agent，避免被拦截
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class WebSearchTool:
    """web_search 工具适配器（T3-4）。

    通过 DuckDuckGo HTML 端点搜索，返回 Top5 结果。
    """

    name = "web_search"
    description = (
        "搜索互联网获取最新信息。输入查询关键词，返回 Top5 搜索结果"
        "（标题、链接、摘要）。适用于需要实时信息、新闻、技术文档的场景。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索查询关键词",
                "minLength": 1,
                "maxLength": 500,
            },
            "max_results": {
                "type": "integer",
                "description": "返回结果数量（默认5，最大10）",
                "minimum": 1,
                "maximum": 10,
                "default": 5,
            },
        },
        "required": ["query"],
    }

    def __init__(self) -> None:
        """初始化工具，读取配置。"""
        config = get_config()
        self.timeout = getattr(config, "mcp_tool_timeout", 30)
        self.cache_ttl = getattr(config, "mcp_search_cache_ttl", 300)

    def execute(self, arguments: dict[str, Any]) -> str:
        """执行网络搜索。

        Args:
            arguments: 参数字典，含 query（必填）、max_results（可选）

        Returns:
            搜索结果的文本格式（JSON 字符串）

        Raises:
            AppException(TOOL_TIMEOUT): 搜索超时时抛出
            AppException(TOOL_EXECUTION_ERROR): 搜索失败时抛出
        """
        query = arguments["query"].strip()
        max_results = min(arguments.get("max_results", 5), 10)

        # 检查缓存
        cache_key = query.lower()
        if cache_key in _cache:
            timestamp, results = _cache[cache_key]
            if time.time() - timestamp < self.cache_ttl:
                logger.info("web_search_cache_hit", query=query)
                return self._format_results(results[:max_results])

        # 构造 DuckDuckGo HTML 搜索 URL
        url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"

        try:
            req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
            with urllib.request.urlopen(req, timeout=min(self.timeout, 15)) as response:
                html = response.read().decode("utf-8", errors="replace")
        except TimeoutError as e:
            logger.error("web_search_timeout", query=query, error=str(e))
            raise AppException(ErrorCode.TOOL_TIMEOUT, f"搜索超时: {query}") from e
        except Exception as e:
            logger.error("web_search_failed", query=query, error=str(e))
            # 搜索失败时返回结构化错误，不阻断 Agent 流程
            return self._format_error(f"搜索失败: {e}")

        # 解析 HTML
        results = self._parse_duckduckgo_html(html, max_results)

        # 写入缓存
        _cache[cache_key] = (time.time(), results)
        # 清理过期缓存（简单策略，超过100条时清理）
        if len(_cache) > 100:
            now = time.time()
            expired = [k for k, (ts, _) in _cache.items() if now - ts > self.cache_ttl]
            for k in expired:
                del _cache[k]

        logger.info("web_search_success", query=query, results=len(results))
        return self._format_results(results)

    def _parse_duckduckgo_html(self, html: str, max_results: int) -> list[dict[str, str]]:
        """解析 DuckDuckGo HTML 搜索结果。

        Args:
            html: HTML 页面内容
            max_results: 最大结果数

        Returns:
            搜索结果列表，每项含 title/url/snippet
        """
        soup = BeautifulSoup(html, "html.parser")
        results: list[dict[str, str]] = []

        # DuckDuckGo HTML 结果在 .result__a 和 .result__snippet 中
        for result_div in soup.select(".result"):
            if len(results) >= max_results:
                break

            title_elem = result_div.select_one(".result__a")
            snippet_elem = result_div.select_one(".result__snippet")

            if not title_elem:
                continue

            title = title_elem.get_text(strip=True)
            # DuckDuckGo 的链接是重定向 URL，需要提取实际 URL
            raw_url = title_elem.get("href", "")
            url = self._extract_actual_url(raw_url)
            snippet = snippet_elem.get_text(strip=True) if snippet_elem else ""

            if title and url:
                results.append({"title": title, "url": url, "snippet": snippet})

        return results

    @staticmethod
    def _extract_actual_url(redirect_url: str) -> str:
        """从 DuckDuckGo 重定向 URL 中提取实际目标 URL。

        Args:
            redirect_url: DuckDuckGo 重定向链接

        Returns:
            实际目标 URL
        """
        if "uddg=" in redirect_url:
            # 格式：//duckduckgo.com/l/?uddg=<encoded_url>&rut=...
            parsed = urllib.parse.urlparse(redirect_url)
            params = urllib.parse.parse_qs(parsed.query)
            if "uddg" in params:
                return urllib.parse.unquote(params["uddg"][0])
        return redirect_url

    @staticmethod
    def _format_results(results: list[dict[str, str]]) -> str:
        """格式化搜索结果为文本。

        Args:
            results: 搜索结果列表

        Returns:
            格式化的文本结果
        """
        if not results:
            return "未找到相关搜索结果。"

        lines = [f"搜索结果（共 {len(results)} 条）：", ""]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r['title']}")
            lines.append(f"   链接: {r['url']}")
            if r.get("snippet"):
                lines.append(f"   摘要: {r['snippet']}")
            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def _format_error(message: str) -> str:
        """格式化错误信息。

        Args:
            message: 错误消息

        Returns:
            格式化的错误文本
        """
        return f"[搜索错误] {message}\n\n建议：请检查网络连接，或尝试使用其他关键词。"
