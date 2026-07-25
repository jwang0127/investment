"""无需账号的新闻/政策 RSS 兜底，以及可选 JSON 新闻 API。"""

from __future__ import annotations

import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


def _rss(query: str, category: str, limit: int = 5) -> list[dict]:
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": query, "hl": "zh-CN", "gl": "CN", "ceid": "CN:zh-Hans"})
    try:
        with urllib.request.urlopen(url, timeout=12) as response:
            root = ET.fromstring(response.read())
        rows = []
        for item in root.findall("./channel/item")[:limit]:
            rows.append({"title": item.findtext("title", ""), "link": item.findtext("link", ""), "published": item.findtext("pubDate", ""), "category": category})
        return rows
    except Exception:
        return []


def fetch_news() -> list[dict]:
    """优先使用可配置 JSON API，否则使用公开 RSS，不把新闻写成确定性行情结论。"""
    rows = []
    rows.extend(_rss("A股 市场 成交量 行业轮动", "市场", 5))
    rows.extend(_rss("中国 证监会 央行 财政部 股市 政策", "政策", 5))
    api_url = os.getenv("NEWS_API_URL", "").strip()
    if api_url:
        try:
            with urllib.request.urlopen(api_url, timeout=12) as response:
                data = __import__("json").loads(response.read())
            if isinstance(data, list):
                rows = data[:10] + rows
        except Exception:
            pass
    return rows[:10]
