"""无需账号的新闻/政策 RSS 兜底，以及可选 JSON 新闻 API。"""

from __future__ import annotations

import calendar
import json
import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from email.utils import parsedate_to_datetime


def _clean_title(title: str) -> str:
    """去掉 Google News 标题的“ - 媒体名”后缀，用于展示与去重。"""
    cleaned = (title or "").rsplit(" - ", 1)[0].strip()
    return cleaned or (title or "")


def _rss(query: str, category: str, as_of: date, limit: int = 5, warnings: list[str] | None = None) -> list[dict]:
    start = as_of - timedelta(days=7)
    filtered_query = f"{query} after:{start.isoformat()} before:{(as_of + timedelta(days=1)).isoformat()}"
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": filtered_query, "hl": "zh-CN", "gl": "CN", "ceid": "CN:zh-Hans"})
    try:
        with urllib.request.urlopen(url, timeout=12) as response:
            root = ET.fromstring(response.read())
    except Exception as exc:
        if warnings is not None:
            warnings.append(f"新闻源（{category}）抓取失败：{type(exc).__name__}")
        return []
    rows = []
    for item in root.findall("./channel/item")[:limit]:
        published_raw = item.findtext("pubDate", "")
        try:
            # RFC822 允许 1 位日期且月/星期缩写依赖 locale，必须用 email.utils 解析。
            published = parsedate_to_datetime(published_raw).date()
        except (TypeError, ValueError):
            continue
        if start <= published <= as_of:
            rows.append({"title": item.findtext("title", ""), "link": item.findtext("link", ""), "published": published_raw, "category": category})
    return rows


def fetch_news(as_of: date | None = None, warnings: list[str] | None = None) -> list[dict]:
    """优先使用可配置 JSON API，否则使用公开 RSS，不把新闻写成确定性行情结论。"""
    as_of = as_of or date.today()
    rows = _rss("A股 市场 成交量 行业轮动", "市场", as_of, 5, warnings)
    rows += _rss("中国 证监会 央行 财政部 股市 政策", "政策", as_of, 5, warnings)
    api_url = os.getenv("NEWS_API_URL", "").strip()
    if api_url and not api_url.startswith("https://"):
        if warnings is not None:
            warnings.append("NEWS_API_URL 仅支持 https，已忽略")
        api_url = ""
    if api_url:
        try:
            with urllib.request.urlopen(api_url, timeout=12) as response:
                data = json.loads(response.read())
            if isinstance(data, list):
                rows = [r for r in data if isinstance(r, dict) and r.get("title")][:10] + rows
        except Exception as exc:
            if warnings is not None:
                warnings.append(f"新闻 API 拉取失败：{type(exc).__name__}")
    seen: set[str] = set()
    deduped = []
    for row in rows:  # 两条查询词有交集，同一事件会重复出现
        key = _clean_title(row.get("title", ""))
        if key and key not in seen:
            seen.add(key)
            deduped.append(row)
    return deduped[:10]


def policy_expectations(as_of: date) -> list[dict]:
    """只输出有明确节奏或公开日程依据的预期，并把推断标为推断。"""
    year, month = as_of.year, as_of.month
    next_month = month % 12 + 1
    next_year = year + (1 if month == 12 else 0)
    pmi_last_day = calendar.monthrange(year, month)[1]
    # 本月 20 日未过时，最近一次 LPR 报价就在本月，不能整月跳过。
    lpr_year, lpr_month = (year, month) if as_of.day < 20 else (next_year, next_month)
    return [
        {"item": f"{month}月官方制造业/非制造业PMI", "expected_time": f"{year}-{month:02d}-{pmi_last_day}前后 09:30", "type": "数据", "basis": "国家统计局月度发布惯例", "certainty": "中（节奏推断）"},
        {"item": f"{lpr_month}月LPR报价", "expected_time": f"{lpr_year}-{lpr_month:02d}-20前后 09:15", "type": "政策", "basis": "全国银行间同业拆借中心月度报价节奏", "certainty": "中（具体日期以公告为准）"},
        {"item": f"{month}月外汇储备与进出口数据", "expected_time": f"{next_year}-{next_month:02d}月上旬公布", "type": "数据", "basis": "海关/外汇管理部门常规发布时间", "certainty": "中（具体日期以官方日程为准）"},
    ]


def abstract_news(rows: list[dict]) -> list[dict]:
    """把标题压缩成可读的研究线索；没有模型 Key 时也能稳定产出结构化摘要。"""
    results = []
    for row in rows[:10]:
        raw_title = row.get("title", "")
        title = _clean_title(raw_title)
        if any(k in title for k in ("降准", "降息", "流动性", "货币")):
            theme, impact = "流动性", "偏利好风险资产，但仍需成交量确认"
        elif any(k in title for k in ("监管", "处罚", "规则", "证监会")):
            theme, impact = "监管政策", "影响相关行业估值和交易行为，需区分短期冲击与长期规范"
        elif any(k in title for k in ("财政", "专项债", "经济", "GDP", "制造业")):
            theme, impact = "宏观与财政", "影响市场风险偏好和周期行业预期"
        elif any(k in title for k in ("芯片", "半导体", "人工智能", "新能源")):
            theme, impact = "产业主题", "可能影响主题行业相对强度，但不能替代业绩验证"
        elif any(k in title for k in ("伊朗", "美伊", "霍尔木兹", "中东", "战争", "地缘")):
            theme, impact = "地缘政治", "通过油价、避险情绪、汇率和海外风险偏好影响A股；需等待事实进展确认"
        else:
            theme, impact = row.get("category", "市场信息"), "作为背景信息观察，不单独构成交易信号"
        results.append({"theme": theme, "title": title, "raw_title": raw_title, "impact": impact, "confidence": "中", "link": row.get("link", ""), "published": row.get("published", "")})
    return results
