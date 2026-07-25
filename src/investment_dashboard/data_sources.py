"""免费行情适配器。失败时返回空数据并保留错误原因，不伪造行情。"""

from __future__ import annotations

import importlib
from dataclasses import dataclass

import pandas as pd
import requests


@dataclass
class DataBundle:
    market: pd.DataFrame
    industries: pd.DataFrame
    stocks: pd.DataFrame
    source: str
    warning: str = ""
    indices: dict[str, dict] | None = None
    news: list[dict] | None = None
    breadth: dict | None = None
    policy_expectations: list[dict] | None = None


def _empty() -> DataBundle:
    return DataBundle(pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), "none", "暂无可用行情数据")


def _industry_direct() -> pd.DataFrame:
    """Eastmoney 公共板块接口兜底；不需要 Token。"""
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {"pn": 1, "pz": 100, "po": 1, "np": 1, "fltt": 2, "invt": 2, "fid": "f3", "fs": "m:90 t:2 f:!50", "fields": "f2,f3,f4,f5,f6,f7,f8,f12,f14,f104,f105"}
    response = requests.get(url, params=params, timeout=12, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()
    items = (response.json().get("data") or {}).get("diff") or []
    rows = []
    for item in items:
        up, down = float(item.get("f104") or 0), float(item.get("f105") or 0)
        rows.append({"industry": item.get("f14", ""), "pct_chg": float(item.get("f3") or 0), "breadth": up / max(up + down, 1) * 100, "close": float(item.get("f2") or 0), "amount": float(item.get("f6") or 1)})
    return pd.DataFrame(rows)


def _breadth_direct() -> dict:
    """读取全市场涨跌家数，作为 AkShare 快照接口的免费兜底。"""
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {"pn": 1, "pz": 6000, "po": 1, "np": 1, "fltt": 2, "invt": 2, "fid": "f3", "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23", "fields": "f3"}
    response = requests.get(url, params=params, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()
    items = ((response.json().get("data") or {}).get("diff") or [])
    changes = [float(x.get("f3")) for x in items if x.get("f3") not in (None, "-")]
    return {"up": sum(x > 0 for x in changes), "down": sum(x < 0 for x in changes), "flat": sum(x == 0 for x in changes), "total": len(changes)}


def _index_amount_direct(days: int) -> pd.DataFrame:
    """Eastmoney 指数 K 线接口：f56 是成交额，避免把 f55 成交量误标为成交额。"""
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    frames = []
    for secid in ("1.000001", "0.399001"):
        params = {"secid": secid, "klt": 101, "fqt": 1, "beg": "20260101", "end": "20991231", "fields1": "f1,f2,f3,f4", "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"}
        response = requests.get(url, params=params, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        rows = []
        for line in ((response.json().get("data") or {}).get("klines") or [])[-days:]:
            parts = line.split(",")
            if len(parts) >= 11:
                rows.append({"date": parts[0], "close": float(parts[2]), "amount": float(parts[6]), "pct_chg": float(parts[8])})
        frames.append(pd.DataFrame(rows))
    sh, sz = frames
    if sh.empty or sz.empty:
        return sh
    if not sz.empty:
        sh = sh.merge(sz[["date", "amount"]].rename(columns={"amount": "sz_amount"}), on="date", how="left")
        sh["amount"] = sh["amount"] + sh["sz_amount"].fillna(0)
        sh = sh.drop(columns=["sz_amount"])
    return sh


def fetch_free_data(days: int = 120) -> DataBundle:
    try:
        ak = importlib.import_module("akshare")
    except ImportError:
        try:
            market = _index_amount_direct(days)
        except Exception as exc:
            return DataBundle(pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), "none", f"AkShare 未安装，备用行情接口也失败：{exc}")
        if market.empty:
            return DataBundle(pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), "none", "AkShare 未安装，备用行情接口没有返回数据")
        return DataBundle(market, pd.DataFrame(), pd.DataFrame(), "eastmoney", "AkShare 未安装，已使用备用行情接口")
    try:
        # AkShare 接口经常随上游调整；每一步都独立容错，保证报告可生成。
        market_error = ""
        try:
            market = ak.stock_zh_index_daily(symbol="sh000001")
        except Exception as exc:
            market = pd.DataFrame()
            market_error = str(exc)
        if market.empty:
            try:
                market = _index_amount_direct(days)
                market_source = "eastmoney"
            except Exception as exc:
                return DataBundle(pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), "none", f"主行情接口失败：{market_error}；备用行情接口失败：{exc}")
        else:
            market_source = "akshare"
        market = market.rename(columns={"date": "date", "close": "close", "成交额": "amount"})
        base_market = market.copy()
        try:
            direct_market = _index_amount_direct(days)
            market = direct_market if not direct_market.empty else base_market
        except Exception:
            # 原接口的 volume 是成交量，不是成交额；金额未知时明确留空。
            market = base_market
            market["amount"] = pd.NA
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
        breadth = None
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
            if industries.empty:
                industries = _industry_direct()
                industries["date"] = today
        except Exception:
            try:
                industries = _industry_direct()
                industries["date"] = today
            except Exception:
                industries = pd.DataFrame()
        all_changes = pd.Series(dtype=float)
        try:
            raw_stocks = ak.stock_zh_a_spot_em()
            rename = {"代码": "code", "名称": "name", "最新价": "price", "涨跌幅": "pct_chg", "成交额": "amount", "换手率": "turnover", "市盈率-动态": "pe"}
            stocks = raw_stocks.rename(columns=rename)
            all_changes = pd.to_numeric(stocks.get("pct_chg", pd.Series(dtype=float)), errors="coerce").dropna()
            needed = [c for c in ["code", "name", "price", "pct_chg", "amount", "turnover", "pe"] if c in stocks.columns]
            stocks = stocks[needed]
            for col in ["price", "pct_chg", "amount", "turnover", "pe"]:
                if col in stocks:
                    stocks[col] = pd.to_numeric(stocks[col], errors="coerce")
            stocks = stocks.dropna(subset=["code", "price", "pct_chg"]).sort_values(["pct_chg", "amount"], ascending=False).head(30)
        except Exception:
            stocks = pd.DataFrame()
        try:
            if len(all_changes):
                changes = all_changes
                breadth = {"up": int((changes > 0).sum()), "down": int((changes < 0).sum()), "flat": int((changes == 0).sum()), "total": int(len(changes))}
            else:
                breadth = _breadth_direct()
        except Exception:
            try:
                breadth = _breadth_direct()
            except Exception:
                breadth = None
        warning = f"主行情接口失败，已切换备用行情接口：{market_error}" if market_source == "eastmoney" and market_error else ""
        return DataBundle(market, industries, stocks, market_source, warning, indices=indices, news=[], breadth=breadth)
    except Exception as exc:  # pragma: no cover - depends on upstream network
        return DataBundle(pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), "akshare", f"免费行情源访问失败：{exc}")
