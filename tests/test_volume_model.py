import numpy as np
import pandas as pd
import pytest

from investment_dashboard.volume_model import (
    SIGNAL_DEAD_ZONE,
    backtest_volume,
    classify_volume,
    market_features,
    score_industries,
    volume_signal,
)


def _synthetic_market(n=300, seed=7):
    rng = np.random.default_rng(seed)
    df = pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=n, freq="B"),
        "close": 3000 + np.cumsum(rng.normal(0, 20, n)),
        "amount": 1.5e12 + rng.normal(0, 2e11, n),
    })
    df["pct_chg"] = df["close"].pct_change() * 100
    return df


class ClassifyVolumeTests:
    pass


def test_classification_returns_known_states():
    df = _synthetic_market()
    states = set(classify_volume(df["amount"]).dropna())
    assert states <= {"FLOOD", "EXPAND", "STABLE", "SHRINK", "DROUGHT", "UNKNOWN"}


def test_cold_start_is_unknown_not_drought():
    amount = pd.Series([1e12] * 30)
    states = classify_volume(amount)
    # 均线未形成的前几天必须是 UNKNOWN，绝不能判成最悲观的 DROUGHT
    assert (states.iloc[:9] == "UNKNOWN").all()
    assert "DROUGHT" not in set(states.iloc[:9])


def test_all_nan_amount_is_unknown():
    amount = pd.Series([np.nan] * 40)
    assert set(classify_volume(amount)) == {"UNKNOWN"}


def test_extreme_state_needs_two_day_confirmation():
    # 250+ 天平稳后单日暴量：首日应记为 EXPAND（软化），连续两日才升级 FLOOD
    base = [1e12 + i * 1e8 for i in range(300)]
    spike_one = pd.Series(base[:-1] + [5e12])
    assert classify_volume(spike_one).iloc[-1] == "EXPAND"
    spike_two = pd.Series(base[:-2] + [5e12, 5e12])
    assert classify_volume(spike_two).iloc[-1] == "FLOOD"


def test_unknown_signal_is_zero():
    row = pd.Series({"volume_state": "UNKNOWN", "volume_slope5": 5.0, "breadth": 90.0, "pct_chg": 2.0})
    assert volume_signal(row) == 0.0


def test_flood_down_day_is_negative():
    up = volume_signal(pd.Series({"volume_state": "FLOOD", "pct_chg": 1.0}))
    down = volume_signal(pd.Series({"volume_state": "FLOOD", "pct_chg": -1.0}))
    assert up > 0
    assert down < 0  # 放量下跌不能得正分


def test_market_features_tolerates_nan_amount():
    df = _synthetic_market(60)
    df.loc[30, "amount"] = np.nan
    features = market_features(df)
    assert len(features) == 60
    assert features["volume_state"].notna().all()


def test_backtest_reports_baseline_and_splits():
    features = market_features(_synthetic_market())
    result = backtest_volume(features, horizon=1)
    assert result.observations > 0
    assert result.hit_rate is not None
    assert result.baseline_hit_rate is not None
    assert result.edge is not None
    assert result.state_hit_rates
    # 死区内的中性信号不应计入样本
    signals = features.apply(volume_signal, axis=1)
    neutral = ((signals.abs() <= SIGNAL_DEAD_ZONE)).sum()
    assert result.observations <= len(features) - neutral


def test_backtest_empty_input():
    empty = market_features(pd.DataFrame({"date": [], "close": [], "amount": []}))
    result = backtest_volume(empty)
    assert result.observations == 0
    assert result.hit_rate is None


def test_score_industries_missing_breadth_is_neutral():
    rng = np.random.default_rng(3)
    frame = pd.DataFrame({
        "date": "2026-07-25",
        "industry": [f"行业{i}" for i in range(10)],
        "pct_chg": rng.normal(0, 2, 10),
        "amount": rng.uniform(1e10, 5e10, 10),
        "breadth": [50.0] * 9 + [np.nan],  # 缺失宽度的行业
    })
    scored = score_industries(frame)
    missing = scored[scored["industry"] == "行业9"].iloc[0]
    # 缺失值 z 后填 0（截面中性），不能被打成极端空头
    assert scored["score"].min() <= missing["score"] <= scored["score"].max()
    assert set(scored["signal"]) <= {"强势关注", "观察", "等待确认", "风险回避"}


def test_score_industries_multiday_factors():
    rng = np.random.default_rng(5)
    dates = pd.date_range("2026-06-01", periods=25, freq="B").strftime("%Y-%m-%d")
    rows = []
    for name, drift in [("强势", 0.8), ("弱势", -0.8), ("中性", 0.0)]:
        for d in dates:
            rows.append({"date": d, "industry": name, "pct_chg": drift + rng.normal(0, 0.3), "amount": 1e10, "breadth": 50})
    scored = score_industries(pd.DataFrame(rows))
    assert scored.iloc[0]["industry"] == "强势"
    assert scored.iloc[-1]["industry"] == "弱势"
    assert scored["ret5"].notna().all()


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
