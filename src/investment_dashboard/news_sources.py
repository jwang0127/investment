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


def abstract_news(rows: list[dict]) -> list[dict]:
    """把标题压缩成可读的研究线索；没有模型 Key 时也能稳定产出结构化摘要。"""
    results = []
    for row in rows[:10]:
        title = row.get("title", "")
        if any(k in title for k in ("降准", "降息", "流动性", "货币")):
            theme, impact = "流动性", "偏利好风险资产，但仍需成交量确认"
        elif any(k in title for k in ("监管", "处罚", "规则", "证监会")):
            theme, impact = "监管政策", "影响相关行业估值和交易行为，需区分短期冲击与长期规范"
        elif any(k in title for k in ("财政", "专项债", "经济", "GDP", "制造业")):
            theme, impact = "宏观与财政", "影响市场风险偏好和周期行业预期"
        elif any(k in title for k in ("芯片", "半导体", "人工智能", "新能源")):
            theme, impact = "产业主题", "可能影响主题行业相对强度，但不能替代业绩验证"
        else:
            theme, impact = row.get("category", "市场信息"), "作为背景信息观察，不单独构成交易信号"
        results.append({"theme": theme, "title": title, "impact": impact, "confidence": "中", "link": row.get("link", ""), "published": row.get("published", "")})
    return results
