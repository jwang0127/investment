"""成交量-行业轮动模型的独立实现。

模型只消费已发生的日线数据，输出可解释状态和可回测分数；不在这里访问网络。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd


VOLUME_STATES = ("FLOOD", "EXPAND", "STABLE", "SHRINK", "DROUGHT")


def _safe_ratio(a: float, b: float) -> float:
    return float(a / b) if b and np.isfinite(b) else np.nan


def classify_volume(amount: pd.Series) -> pd.Series:
    ma20 = amount.rolling(20, min_periods=5).mean()
    ratio = amount / ma20.replace(0, np.nan)
    return pd.Series(
        np.select(
            [ratio > 1.5, ratio > 1.1, ratio.between(0.9, 1.1, inclusive="both"), ratio > 0.7],
            ["FLOOD", "EXPAND", "STABLE", "SHRINK"],
            default="DROUGHT",
        ),
        index=amount.index,
        name="volume_state",
    )


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
    ret5 = close.pct_change(5) * 100
    slope5 = amount.rolling(5, min_periods=3).apply(
        lambda x: np.polyfit(np.arange(len(x)), x / np.nanmean(x), 1)[0] * 100,
        raw=True,
    )
    df["amount_ma20"] = ma20
    df["amount_ma60"] = ma60
    df["amount_ratio20"] = amount / ma20.replace(0, np.nan)
    df["amount_ratio60"] = amount / ma60.replace(0, np.nan)
    df["volume_state"] = classify_volume(amount)
    df["volume_slope5"] = slope5
    df["market_return5"] = ret5
    df["breadth"] = pd.to_numeric(df.get("breadth", np.nan), errors="coerce")
    df["pct_chg"] = pct
    return df


def volume_signal(row: pd.Series) -> float:
    """给出 -1 到 1 的可解释市场量能分数。"""
    state_score = {"FLOOD": 0.35, "EXPAND": 0.65, "STABLE": 0.10, "SHRINK": -0.20, "DROUGHT": -0.45}
    score = state_score.get(row.get("volume_state"), 0.0)
    slope = row.get("volume_slope5", 0.0)
    breadth = row.get("breadth", 50.0)
    if pd.notna(slope):
        score += float(np.clip(slope / 10, -0.25, 0.25))
    if pd.notna(breadth):
        score += float(np.clip((float(breadth) - 50) / 200, -0.25, 0.25))
    return float(np.clip(score, -1, 1))


def score_industries(industry: pd.DataFrame, market: pd.DataFrame) -> pd.DataFrame:
    """计算行业横截面分数。需要 date, industry, close, amount, pct_chg。"""
    x = industry.copy()
    x["date"] = pd.to_datetime(x["date"])
    x = x.sort_values(["industry", "date"])
    g = x.groupby("industry", group_keys=False)
    x["ret5"] = g["close"].pct_change(5) * 100
    x["ret20"] = g["close"].pct_change(20) * 100
    x["amount_share"] = x["amount"] / x.groupby("date")["amount"].transform("sum")
    x["share_change5"] = g["amount_share"].diff(5)
    x["breadth"] = x.get("breadth", np.nan)
    latest = x.groupby("industry", as_index=False).tail(1).copy()
    for col in ["ret5", "ret20", "share_change5", "breadth"]:
        latest[col] = pd.to_numeric(latest[col], errors="coerce").fillna(0)
    def z(s: pd.Series) -> pd.Series:
        std = s.std(ddof=0)
        return (s - s.mean()) / std if std and np.isfinite(std) else s * 0
    latest["score"] = (
        45
        + 16 * z(latest["ret5"])
        + 14 * z(latest["ret20"])
        + 12 * z(latest["share_change5"])
        + 8 * z(latest["breadth"])
    ).clip(0, 100).round(2)
    latest["signal"] = np.select(
        [latest["score"] >= 70, latest["score"] >= 55, latest["score"] <= 35],
        ["强势关注", "观察", "风险回避"],
        default="等待确认",
    )
    return latest.sort_values("score", ascending=False).reset_index(drop=True)


@dataclass
class BacktestResult:
    observations: int
    hit_rate: float | None
    average_forward_return: float | None


def backtest_volume(features: pd.DataFrame, horizon: int = 5) -> BacktestResult:
    """验证量能分数方向与未来收益方向的一致性。"""
    df = features.copy().sort_values("date")
    df["signal"] = df.apply(volume_signal, axis=1)
    df["forward_return"] = pd.to_numeric(df["close"], errors="coerce").shift(-horizon) / df["close"] - 1
    df = df.dropna(subset=["signal", "forward_return"])
    if df.empty:
        return BacktestResult(0, None, None)
    hit = ((df["signal"] > 0) == (df["forward_return"] > 0)).mean()
    return BacktestResult(len(df), round(float(hit), 4), round(float(df["forward_return"].mean()), 6))
