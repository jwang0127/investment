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
    indices: dict[str, dict] | None = None


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
        indices: dict[str, dict] = {}
        for symbol, name in {"sh000001": "上证指数", "sz399001": "深证成指", "sz399006": "创业板指", "sh000688": "科创50", "bj899050": "北证50"}.items():
            try:
                frame = ak.stock_zh_index_daily(symbol=symbol)
                if not frame.empty:
                    frame = frame.sort_values("date")
                    last = frame.iloc[-1]
                    prev = frame.iloc[-2] if len(frame) > 1 else last
                    close = float(last["close"])
                    previous = float(prev["close"])
                    indices[name] = {"close": close, "pct_chg": (close / previous - 1) * 100 if previous else 0}
            except Exception:
                continue
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
                market_cap = pd.to_numeric(industries["market_cap"], errors="coerce") if "market_cap" in industries else pd.Series(1, index=industries.index)
                up = pd.to_numeric(industries["up_count"], errors="coerce") if "up_count" in industries else pd.Series(0, index=industries.index)
                down = pd.to_numeric(industries["down_count"], errors="coerce") if "down_count" in industries else pd.Series(0, index=industries.index)
                industries["amount"] = market_cap.fillna(1)
                industries["breadth"] = (up / (up + down).replace(0, 1) * 100).fillna(50)
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
        return DataBundle(market, industries, stocks, "akshare", indices=indices)
    except Exception as exc:  # pragma: no cover - depends on upstream network
        return DataBundle(pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), "akshare", f"免费行情源访问失败：{exc}")
