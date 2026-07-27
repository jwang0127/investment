"""免费行情适配器。失败时返回空数据并保留错误原因，不伪造行情。

东财公共接口为主（成交额、板块成交额均为真实值），AkShare 兜底；
每一步独立容错，降级原因全部累积进 warning，绝不静默。
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd
import requests
from requests.adapters import HTTPAdapter, Retry

_session = requests.Session()
_session.mount("https://", HTTPAdapter(max_retries=Retry(
    total=2, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504], allowed_methods=["GET"],
)))
_UA = {"User-Agent": "Mozilla/5.0"}
_INDEX_SECIDS = "1.000001,0.399001,0.399006,1.000688,0.899050"
_INDEX_SYMBOLS = {
    "sh000001": "上证指数",
    "sz399001": "深证成指",
    "sz399006": "创业板指",
    "sh000688": "科创50",
    "bj899050": "北证50",
}


def _f(v, default=0.0):
    """东财接口对停牌/缺数据返回字符串 '-'，安全转换避免整段兜底崩溃。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _get(url: str, **kwargs):
    """优先 HTTPS；遇到代理断开或 5xx 时切换 HTTP 备用链路。"""
    last_error = None
    candidates = [url]
    if url.startswith("https://"):
        candidates.append(url.replace("https://", "http://", 1))
    if "push2.eastmoney.com" in url:
        candidates.extend([
            url.replace("https://push2.eastmoney.com", "http://82.push2.eastmoney.com"),
            url.replace("http://push2.eastmoney.com", "http://82.push2.eastmoney.com"),
            url.replace("https://push2.eastmoney.com", "http://push2his.eastmoney.com"),
            url.replace("http://push2.eastmoney.com", "http://push2his.eastmoney.com"),
        ])
    elif "push2his.eastmoney.com" in url:
        candidates.extend([
            url.replace("https://push2his.eastmoney.com", "http://82.push2his.eastmoney.com"),
            url.replace("http://push2his.eastmoney.com", "http://82.push2his.eastmoney.com"),
        ])
    candidates = list(dict.fromkeys(candidates))
    for candidate in candidates:
        try:
            response = _session.get(candidate, **kwargs)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
    raise last_error or requests.RequestException("行情接口无响应")


def _get_json(url: str, **kwargs) -> dict:
    """接口偶尔返回空白/HTML 但状态码仍为 200，单次请求再做 JSON 重试。"""
    last_error = None
    for _ in range(3):
        try:
            response = _get(url, **kwargs)
            payload = response.json()
            if isinstance(payload, dict):
                return payload
            last_error = ValueError("行情接口返回格式异常")
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
    raise last_error or ValueError("行情接口返回空数据")


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


def _industry_direct() -> pd.DataFrame:
    """东财板块接口：f6 是真实成交额，f104/f105 涨跌家数给出宽度。"""
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {"pn": 1, "pz": 100, "po": 1, "np": 1, "fltt": 2, "invt": 2, "fid": "f3", "fs": "m:90 t:2 f:!50", "fields": "f2,f3,f4,f5,f6,f7,f8,f12,f14,f104,f105"}
    response = _get(url, params=params, timeout=12, headers=_UA)
    items = (response.json().get("data") or {}).get("diff") or []
    rows = []
    for item in items:
        up, down = _f(item.get("f104")), _f(item.get("f105"))
        rows.append({
            "industry": item.get("f14", ""),
            "pct_chg": _f(item.get("f3")),
            "breadth": up / max(up + down, 1) * 100,
            "close": _f(item.get("f2")),
            "amount": _f(item.get("f6")) or None,
        })
    return pd.DataFrame([r for r in rows if r["industry"]])


def _breadth_direct() -> dict:
    """全市场涨跌家数；东财单页最多返回 100 条，需要按 total 分页。"""
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {"pn": 1, "pz": 100, "po": 1, "np": 1, "fltt": 2, "invt": 2, "fid": "f3", "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23", "fields": "f3"}
    data = _get_json(url, params=params, timeout=15, headers=_UA).get("data") or {}
    items = list(data.get("diff") or [])
    total = int(data.get("total") or len(items))
    page_size = 100
    for page in range(2, min((total + page_size - 1) // page_size, 60) + 1):
        page_params = dict(params, pn=page)
        page_data = _get_json(url, params=page_params, timeout=15, headers=_UA).get("data") or {}
        page_items = page_data.get("diff") or []
        if not page_items:
            break
        items.extend(page_items)
    if total > len(items):
        raise RuntimeError(f"全市场涨跌家数只获取到 {len(items)}/{total} 条")
    changes = [v for v in (_f(x.get("f3"), None) for x in items) if v is not None]
    return {"up": sum(x > 0 for x in changes), "down": sum(x < 0 for x in changes), "flat": sum(x == 0 for x in changes), "total": len(changes)}


def _index_amount_direct(days: int, warnings: list[str] | None = None) -> pd.DataFrame:
    """东财指数 K 线：f56 是成交额（f55 是成交量，两者不能混用）。沪深金额相加。"""
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    beg = (date.today() - timedelta(days=days * 2 + 45)).strftime("%Y%m%d")
    frames: dict[str, pd.DataFrame] = {}
    for secid in ("1.000001", "0.399001"):
        rows = []
        try:
            params = {"secid": secid, "klt": 101, "fqt": 1, "beg": beg, "end": "20991231", "fields1": "f1,f2,f3,f4", "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"}
            response = _get(url, params=params, timeout=15, headers=_UA)
            for line in ((response.json().get("data") or {}).get("klines") or [])[-days:]:
                parts = line.split(",")
                if len(parts) < 11:
                    continue
                close, amount = _f(parts[2], None), _f(parts[6], None)
                if parts[0] and close is not None and amount is not None:
                    rows.append({"date": parts[0], "close": close, "amount": amount, "pct_chg": _f(parts[8], None)})
        except Exception as exc:
            if warnings is not None:
                warnings.append(f"指数K线备用接口不可用（{secid}）")
        frames[secid] = pd.DataFrame(rows)
    sh, sz = frames["1.000001"], frames["0.399001"]
    if sh.empty:
        return sh
    if sz.empty:
        if warnings is not None:
            warnings.append("深证成交额缺失，两市成交额暂不完整")
        return sh
    # inner join：只保留两市都有数据的交易日，避免序列里混入口径不一致的金额。
    merged = sh.merge(sz[["date", "amount"]].rename(columns={"amount": "sz_amount"}), on="date", how="inner")
    merged["amount"] = merged["amount"] + merged["sz_amount"]
    return merged.drop(columns=["sz_amount"])


def _fetch_market(ak, days: int, warnings: list[str]) -> tuple[pd.DataFrame, str]:
    """指数日线 + 两市成交额。东财直连优先（金额真实），AkShare 收盘价兜底。"""
    direct = _index_amount_direct(days, warnings)
    if not direct.empty:
        return direct.tail(days)[["date", "close", "amount", "pct_chg"]].reset_index(drop=True), "eastmoney"
    if ak is None:
        raise RuntimeError("东财指数接口不可用，且 AkShare 未安装，无法获取行情")
    try:
        market = ak.stock_zh_index_daily(symbol="sh000001")
    except Exception as exc:
        raise RuntimeError(f"东财指数接口与 AkShare 均不可用：{exc}") from exc
    if market.empty:
        raise RuntimeError("东财指数接口不可用，AkShare 返回为空")
    market = market.copy()
    # AkShare 该接口的 volume 是成交量不是成交额；金额未知时明确留空。
    market["amount"] = pd.NA
    market["pct_chg"] = pd.to_numeric(market["close"], errors="coerce").pct_change() * 100
    warnings.append("东财成交额接口失败，成交额缺失，量能相关指标不可用")
    return market.tail(days)[["date", "close", "amount", "pct_chg"]].reset_index(drop=True), "akshare"


def _fetch_indices(ak, warnings: list[str]) -> dict[str, dict]:
    """主要指数快照：东财批量接口一次请求；失败回落 AkShare 逐个拉取。"""
    try:
        url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
        params = {"secids": _INDEX_SECIDS, "fields": "f2,f3,f12,f14", "fltt": 2, "invt": 2, "pn": 1, "pz": 10, "po": 1, "np": 1}
        response = _get(url, params=params, timeout=10, headers=_UA)
        items = (response.json().get("data") or {}).get("diff") or []
        indices = {}
        for item in items:
            name, close, pct = item.get("f14"), _f(item.get("f2"), None), _f(item.get("f3"), None)
            if name and close is not None:
                indices[name] = {"close": round(close, 2), "pct_chg": round(pct, 2) if pct is not None else None}
        if indices:
            return indices
    except Exception:
        pass
    if ak is None:
        warnings.append("主要指数快照不可用")
        return {}
    indices = {}
    for symbol, name in _INDEX_SYMBOLS.items():
        try:
            frame = ak.stock_zh_index_daily(symbol=symbol)
            if frame.empty:
                continue
            frame = frame.sort_values("date")
            close = float(frame.iloc[-1]["close"])
            previous = float(frame.iloc[-2]["close"]) if len(frame) > 1 else close
            indices[name] = {"close": round(close, 2), "pct_chg": round((close / previous - 1) * 100, 2) if previous else 0.0}
        except Exception:
            continue
    if not indices:
        warnings.append("主要指数快照不可用")
    return indices


def _fetch_industries(ak, today, warnings: list[str]) -> pd.DataFrame:
    """行业板块。东财直连优先（f6 真实成交额）；AkShare 兜底时成交额为估算值。"""
    try:
        industries = _industry_direct()
        if not industries.empty:
            industries["date"] = today
            return industries
    except Exception as exc:
        warnings.append("东财行业接口不可用，尝试 AkShare 备用")
    if ak is None:
        warnings.append("行业数据不可用")
        return pd.DataFrame()
    try:
        raw = ak.stock_board_industry_name_em()
        rename = {"板块名称": "industry", "涨跌幅": "pct_chg", "总市值": "market_cap", "换手率": "turnover", "上涨家数": "up_count", "下跌家数": "down_count"}
        x = raw.rename(columns=rename)
        if x.empty or "pct_chg" not in x:
            raise ValueError("行业接口字段缺失")
        x["pct_chg"] = pd.to_numeric(x["pct_chg"], errors="coerce")
        x["close"] = 100 + x["pct_chg"].fillna(0)
        market_cap = pd.to_numeric(x.get("market_cap"), errors="coerce")
        turnover = pd.to_numeric(x.get("turnover"), errors="coerce")
        x["amount"] = market_cap * turnover / 100
        up = pd.to_numeric(x.get("up_count"), errors="coerce").fillna(0)
        down = pd.to_numeric(x.get("down_count"), errors="coerce").fillna(0)
        x["breadth"] = (up / (up + down).replace(0, 1) * 100).fillna(50)
        x["date"] = today
        warnings.append("行业成交额为估算值（总市值×换手率），仅供相对比较")
        return x[["date", "industry", "close", "amount", "pct_chg", "breadth"]]
    except Exception as exc:
        warnings.append("行业数据暂不可用")
        return pd.DataFrame()


def _fetch_breadth(ak, warnings: list[str]) -> dict | None:
    try:
        breadth = _breadth_direct()
        if breadth.get("total", 0) >= 1000:
            return breadth
        warnings.append("东财涨跌家数返回样本不足，改用 AkShare 全市场快照")
    except Exception:
        warnings.append("东财涨跌家数接口不可用，改用 AkShare 备用")
    if ak is not None:
        try:
            raw = ak.stock_zh_a_spot_em()
            column = next((c for c in ("涨跌幅", "pct_chg") if c in raw.columns), None)
            if column:
                changes = pd.to_numeric(raw[column], errors="coerce").dropna()
                if len(changes) >= 1000:
                    return {"up": int((changes > 0).sum()), "down": int((changes < 0).sum()), "flat": int((changes == 0).sum()), "total": int(len(changes))}
        except Exception:
            pass
    warnings.append("涨跌家数暂不可用")
    return None


def fetch_free_data(days: int = 320) -> DataBundle:
    warnings: list[str] = []
    try:
        ak = importlib.import_module("akshare")
    except ImportError:
        ak = None
        warnings.append("AkShare 未安装，全部使用东财直连接口")
    try:
        market, source = _fetch_market(ak, days, warnings)
    except Exception as exc:
        warnings.append(str(exc))
        return DataBundle(pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), "none", "；".join(warnings))
    today = market["date"].max()
    indices = _fetch_indices(ak, warnings)
    industries = _fetch_industries(ak, today, warnings)
    breadth = _fetch_breadth(ak, warnings)
    return DataBundle(market, industries, pd.DataFrame(), source, "；".join(warnings), indices=indices, news=[], breadth=breadth)
