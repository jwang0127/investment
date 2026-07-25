"""免费行情适配器。失败时返回空数据并保留错误原因，不伪造行情。"""

from __future__ import annotations

import importlib
from dataclasses import dataclass

import pandas as pd


@dataclass
class DataBundle:
    market: pd.DataFrame
    industries: pd.DataFrame
    stocks: pd.DataFrame
    source: str
    warning: str = ""


def _empty() -> DataBundle:
    return DataBundle(pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), "none", "暂无可用行情数据")


def fetch_free_data(days: int = 120) -> DataBundle:
    try:
        ak = importlib.import_module("akshare")
    except ImportError:
        return _empty()
    try:
        # AkShare 接口经常随上游调整；每一步都独立容错，保证报告可生成。
        market = ak.stock_zh_index_daily(symbol="sh000001")
        market = market.rename(columns={"date": "date", "close": "close", "成交额": "amount"})
        if "amount" not in market:
            market["amount"] = pd.to_numeric(market.get("volume", 0), errors="coerce")
        market["pct_chg"] = market["close"].pct_change() * 100
        market = market.tail(days)[["date", "close", "amount", "pct_chg"]]
        today = market["date"].max()
        industries = pd.DataFrame()
        stocks = pd.DataFrame()
        try:
            raw = ak.stock_board_industry_name_em()
            rename = {"板块名称": "industry", "涨跌幅": "pct_chg", "总市值": "market_cap", "换手率": "turnover", "上涨家数": "up_count", "下跌家数": "down_count"}
            industries = raw.rename(columns=rename)
            industries["date"] = today
            if "pct_chg" in industries:
                industries["pct_chg"] = pd.to_numeric(industries["pct_chg"], errors="coerce")
                industries["close"] = 100 + industries["pct_chg"].fillna(0)
                industries["amount"] = pd.to_numeric(industries.get("market_cap", 1), errors="coerce").fillna(1)
                industries["breadth"] = pd.to_numeric(industries.get("up_count", 0), errors="coerce") / (pd.to_numeric(industries.get("up_count", 0), errors="coerce") + pd.to_numeric(industries.get("down_count", 0), errors="coerce")).replace(0, 1) * 100
                industries = industries[["date", "industry", "close", "amount", "pct_chg", "breadth"]]
        except Exception:
            industries = pd.DataFrame()
        try:
            raw_stocks = ak.stock_zh_a_spot_em()
            rename = {"代码": "code", "名称": "name", "最新价": "price", "涨跌幅": "pct_chg", "成交额": "amount", "换手率": "turnover", "市盈率-动态": "pe"}
            stocks = raw_stocks.rename(columns=rename)
            needed = [c for c in ["code", "name", "price", "pct_chg", "amount", "turnover", "pe"] if c in stocks.columns]
            stocks = stocks[needed]
            for col in ["price", "pct_chg", "amount", "turnover", "pe"]:
                if col in stocks:
                    stocks[col] = pd.to_numeric(stocks[col], errors="coerce")
            stocks = stocks.dropna(subset=["code", "price", "pct_chg"]).sort_values(["pct_chg", "amount"], ascending=False).head(30)
        except Exception:
            stocks = pd.DataFrame()
        return DataBundle(market, industries, stocks, "akshare")
    except Exception as exc:  # pragma: no cover - depends on upstream network
        return DataBundle(pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), "akshare", f"免费行情源访问失败：{exc}")
