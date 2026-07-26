"""成交量-行业轮动模型的独立实现。

模型只消费已发生的日线数据，输出可解释状态和可回测分数；不在这里访问网络。

设计要点：
- 冷启动/缺数据输出 UNKNOWN，绝不把缺数据当 DROUGHT（最悲观状态）。
- 量能阈值优先用近一年滚动分位数自适应（比值向 1 回归，固定 0.7/1.5 几乎不可达），
  样本不足时回退固定阈值。
- FLOOD/DROUGHT 需连续两日确认，首日先记为相邻温和态，减少单日翻转噪声。
- 量能分数与当日价格方向联动：放量下跌（恐慌/出货）不给正分。
- 回测输出基线对照（"始终看多"）、多空拆分与分状态胜率，胜率不与基线比较没有意义。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


VOLUME_STATES = ("FLOOD", "EXPAND", "STABLE", "SHRINK", "DROUGHT", "UNKNOWN")

SIGNAL_DEAD_ZONE = 0.10  # |signal| 低于该值视为不给方向


def _fixed_state(ratio: pd.Series) -> np.ndarray:
    return np.select(
        [ratio.isna(), ratio > 1.5, ratio > 1.1, ratio >= 0.9, ratio > 0.7],
        ["UNKNOWN", "FLOOD", "EXPAND", "STABLE", "SHRINK"],
        default="DROUGHT",
    )


def _percentile_state(pctl: pd.Series) -> np.ndarray:
    return np.select(
        [pctl.isna(), pctl >= 0.90, pctl >= 0.70, pctl >= 0.30, pctl >= 0.10],
        ["UNKNOWN", "FLOOD", "EXPAND", "STABLE", "SHRINK"],
        default="DROUGHT",
    )


def volume_ratio_percentile(amount: pd.Series) -> pd.Series:
    """当日 量/20日均量 比值在近 250 个交易日中的分位。"""
    amount = pd.to_numeric(amount, errors="coerce")
    ma20 = amount.rolling(20, min_periods=10).mean()
    ratio = amount / ma20.replace(0, np.nan)
    return ratio.rolling(250, min_periods=60).rank(pct=True)


def classify_volume(amount: pd.Series) -> pd.Series:
    """量能五态分类 + UNKNOWN。分位数自适应优先，样本不足回退固定阈值。"""
    amount = pd.to_numeric(amount, errors="coerce")
    ma20 = amount.rolling(20, min_periods=10).mean()
    ratio = amount / ma20.replace(0, np.nan)
    pctl = ratio.rolling(250, min_periods=60).rank(pct=True)
    adaptive = _percentile_state(pctl)
    fixed = _fixed_state(ratio)
    raw = pd.Series(np.where(pctl.isna(), fixed, adaptive), index=amount.index, name="volume_state")
    # 极端态两日确认：FLOOD/DROUGHT 首日先记为相邻温和态，连续两日才确认。
    soften = {"FLOOD": "EXPAND", "DROUGHT": "SHRINK"}
    confirmed = raw.where(~raw.isin(list(soften)) | (raw == raw.shift(1)), raw.map(soften))
    return confirmed.rename("volume_state")


def _slope(window: np.ndarray) -> float:
    """5 日量能斜率；窗口内 NaN 剔除后不足 3 点或均值为 0 时返回 NaN，不让 polyfit 崩溃。"""
    x = window[np.isfinite(window)]
    if len(x) < 3 or x.mean() == 0:
        return np.nan
    return float(np.polyfit(np.arange(len(x)), x / x.mean(), 1)[0] * 100)


def market_features(index: pd.DataFrame) -> pd.DataFrame:
    """从包含 date, close, amount, pct_chg 的指数日线生成量能特征。"""
    df = index.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    amount = pd.to_numeric(df["amount"], errors="coerce")
    close = pd.to_numeric(df["close"], errors="coerce")
    pct = pd.to_numeric(df.get("pct_chg", close.pct_change() * 100), errors="coerce")
    ma20 = amount.rolling(20, min_periods=5).mean()
    ma60 = amount.rolling(60, min_periods=10).mean()
    df["amount_ma20"] = ma20
    df["amount_ma60"] = ma60
    df["amount_ratio20"] = amount / ma20.replace(0, np.nan)
    df["amount_ratio60"] = amount / ma60.replace(0, np.nan)
    df["amount_ratio20_pctl"] = volume_ratio_percentile(amount)
    df["volume_state"] = classify_volume(amount)
    df["volume_slope5"] = amount.rolling(5, min_periods=3).apply(_slope, raw=True)
    df["market_return5"] = close.pct_change(5) * 100
    df["breadth"] = pd.to_numeric(df.get("breadth", np.nan), errors="coerce")
    df["pct_chg"] = pct
    return df


def _state_score(state: str, pct_chg: float) -> float:
    """状态×当日方向条件打分：放量下跌是恐慌/出货，不能与放量上涨同分。"""
    up = pd.isna(pct_chg) or pct_chg >= 0
    table = {
        "FLOOD": (0.30, -0.45),
        "EXPAND": (0.55, -0.30),
        "STABLE": (0.10, -0.05),
        "SHRINK": (-0.05, -0.25),
        "DROUGHT": (-0.20, -0.35),
    }
    if state not in table:
        return 0.0
    return table[state][0 if up else 1]


def volume_signal(row: pd.Series) -> float:
    """给出 -1 到 1 的可解释市场量能分数；UNKNOWN 状态直接 0，不叠加其他项。"""
    state = row.get("volume_state")
    if state not in ("FLOOD", "EXPAND", "STABLE", "SHRINK", "DROUGHT"):
        return 0.0
    score = _state_score(state, row.get("pct_chg", np.nan))
    slope = row.get("volume_slope5", np.nan)
    breadth = row.get("breadth", np.nan)
    if pd.notna(slope):
        score += 0.25 * float(np.tanh(float(slope) / 8))  # 平滑替代 clip，避免 |slope|>2.5 就饱和
    if pd.notna(breadth):
        score += float(np.clip((float(breadth) - 50) / 200, -0.25, 0.25))
    return float(np.clip(score, -1, 1))


def score_industries(industry: pd.DataFrame, market: pd.DataFrame | None = None) -> pd.DataFrame:
    """计算行业横截面分数。需要 date, industry, pct_chg，可选 amount, breadth。

    时序因子（ret5/ret20/份额变化）由 pct_chg 逐日累乘重建的相对净值计算，
    与数据源的 close 口径无关；缺失因子先算 z 再填 0（截面中性），并按可用因子
    重归一权重，避免单日快照时分数被压缩在 45 附近。
    """
    x = industry.copy()
    x["date"] = pd.to_datetime(x["date"])
    x = x.sort_values(["industry", "date"])
    x["pct_chg"] = pd.to_numeric(x["pct_chg"], errors="coerce")
    x["amount"] = pd.to_numeric(x.get("amount", np.nan), errors="coerce")
    g = x.groupby("industry", group_keys=False)
    x["nav"] = g["pct_chg"].apply(lambda s: (1 + s.fillna(0) / 100).cumprod())
    x["ret5"] = g["nav"].pct_change(5) * 100
    x["ret20"] = g["nav"].pct_change(20) * 100
    x["amount_share"] = x["amount"] / x.groupby("date")["amount"].transform("sum")
    x["share_change5"] = g["amount_share"].diff(5)
    x["breadth"] = pd.to_numeric(x.get("breadth", np.nan), errors="coerce")
    latest = x.groupby("industry", as_index=False).tail(1).copy()

    def z(s: pd.Series) -> pd.Series:
        s = pd.to_numeric(s, errors="coerce")
        std = s.std(ddof=0, skipna=True)
        if not std or not np.isfinite(std):
            return pd.Series(0.0, index=s.index)
        return ((s - s.mean(skipna=True)) / std).fillna(0)  # 先 z 后填 0：缺失即截面中性

    weights = {"ret5": 16, "ret20": 14, "share_change5": 12, "breadth": 8}
    available = {k: w for k, w in weights.items() if pd.to_numeric(latest[k], errors="coerce").notna().any()}
    scale = sum(weights.values()) / sum(available.values()) if available else 1.0
    contribution = sum(w * scale * z(latest[k]) for k, w in available.items())
    latest["score"] = (45 + contribution).clip(0, 100).round(2) if available else 45.0
    latest["signal"] = np.select(
        [latest["score"] >= 70, latest["score"] >= 55, latest["score"] <= 35],
        ["强势关注", "观察", "风险回避"],
        default="等待确认",
    )
    keep = ["date", "industry", "score", "signal", "ret5", "ret20", "share_change5", "breadth", "pct_chg", "amount"]
    latest = latest[[c for c in keep if c in latest.columns]]
    return latest.sort_values("score", ascending=False).reset_index(drop=True)


@dataclass
class BacktestResult:
    observations: int
    hit_rate: float | None
    average_forward_return: float | None
    baseline_hit_rate: float | None = None  # 同一样本上“始终看多”的胜率，模型必须与它比较
    edge: float | None = None  # hit_rate 相对更强一侧常数策略的超额
    long_hit_rate: float | None = None
    short_hit_rate: float | None = None
    long_forward_return: float | None = None
    short_forward_return: float | None = None
    state_hit_rates: dict = field(default_factory=dict)
    quintiles: list = field(default_factory=list)  # 信号五分位 → 未来收益，检验分数单调性


def _rate(mask: pd.Series) -> float | None:
    return round(float(mask.mean()), 4) if len(mask) else None


def backtest_volume(features: pd.DataFrame, horizon: int = 5) -> BacktestResult:
    """验证量能分数方向与未来收益方向的一致性。

    horizon>1 时前瞻窗口互相重叠，样本存在序列相关，observations 是乐观值。
    中性信号（|signal| <= 死区）不计入方向胜率；UNKNOWN/热身期行剔除。
    """
    df = features.copy().sort_values("date")
    df = df[df["volume_state"].isin(["FLOOD", "EXPAND", "STABLE", "SHRINK", "DROUGHT"])]
    df["signal"] = df.apply(volume_signal, axis=1)
    close = pd.to_numeric(df["close"], errors="coerce")
    df["forward_return"] = close.shift(-horizon) / close - 1
    df = df.dropna(subset=["signal", "forward_return"])
    if df.empty:
        return BacktestResult(0, None, None)
    longs = df[df["signal"] > SIGNAL_DEAD_ZONE]
    shorts = df[df["signal"] < -SIGNAL_DEAD_ZONE]
    directional = pd.concat([longs, shorts])
    hits = pd.concat([longs["forward_return"] > 0, shorts["forward_return"] < 0])
    baseline = _rate(directional["forward_return"] > 0)
    hit_rate = _rate(hits)
    edge = None
    if hit_rate is not None and baseline is not None:
        edge = round(hit_rate - max(baseline, 1 - baseline), 4)
    state_hits = {}
    for state, group in directional.groupby("volume_state"):
        ok = (group["signal"] > 0) == (group["forward_return"] > 0)
        state_hits[str(state)] = {"observations": int(len(group)), "hit_rate": _rate(ok)}
    quintiles = []
    if df["signal"].nunique() >= 5:
        try:
            buckets = pd.qcut(df["signal"], 5, labels=["Q1", "Q2", "Q3", "Q4", "Q5"], duplicates="drop")
            for name, group in df.groupby(buckets, observed=True):
                quintiles.append({
                    "bucket": str(name),
                    "observations": int(len(group)),
                    "avg_signal": round(float(group["signal"].mean()), 4),
                    "avg_forward_return": round(float(group["forward_return"].mean()), 6),
                    "win_rate": _rate(group["forward_return"] > 0),
                })
        except ValueError:
            pass
    return BacktestResult(
        observations=int(len(directional)),
        hit_rate=hit_rate,
        average_forward_return=round(float(df["forward_return"].mean()), 6),
        baseline_hit_rate=baseline,
        edge=edge,
        long_hit_rate=_rate(longs["forward_return"] > 0),
        short_hit_rate=_rate(shorts["forward_return"] < 0),
        long_forward_return=round(float(longs["forward_return"].mean()), 6) if len(longs) else None,
        short_forward_return=round(float(shorts["forward_return"].mean()), 6) if len(shorts) else None,
        state_hit_rates=state_hits,
        quintiles=quintiles,
    )
