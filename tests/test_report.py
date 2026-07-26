import json

import numpy as np
import pandas as pd

from investment_dashboard.data_sources import DataBundle
from investment_dashboard.report import build_payload, clean_payload, render_markdown, write_outputs


def _bundle(n=80, with_industries=True):
    rng = np.random.default_rng(11)
    market = pd.DataFrame({
        "date": pd.date_range("2026-03-01", periods=n, freq="B").strftime("%Y-%m-%d"),
        "close": 3000 + np.cumsum(rng.normal(0, 15, n)),
        "amount": 1.4e12 + rng.normal(0, 1e11, n),
    })
    market["pct_chg"] = market["close"].pct_change() * 100
    industries = pd.DataFrame()
    if with_industries:
        industries = pd.DataFrame({
            "date": market["date"].iloc[-1],
            "industry": [f"行业{i}" for i in range(12)],
            "close": 100 + rng.normal(0, 2, 12),
            "amount": rng.uniform(1e10, 8e10, 12),
            "pct_chg": rng.normal(0, 2, 12),
            "breadth": rng.uniform(20, 80, 12),
        })
    return DataBundle(
        market, industries, pd.DataFrame(), "test", "",
        indices={"上证指数": {"close": 3968.96, "pct_chg": 0.71}},
        news=[], breadth={"up": 3000, "down": 2000, "flat": 400, "total": 5400},
    )


def test_payload_structure_and_strict_json(tmp_path):
    payload = clean_payload(build_payload(_bundle()))
    assert payload["status"] == "ok"
    market = payload["market"]
    assert market["volume_state"] in {"FLOOD", "EXPAND", "STABLE", "SHRINK", "DROUGHT", "UNKNOWN"}
    assert market["score"]["total"] is None or 0 <= market["score"]["total"] <= 100
    assert len(payload["chart"]["history"]) == 30
    assert payload["backtest"]  # 回测必须接进 payload，不再是死代码
    assert payload["industries_top"] and payload["industries_score"]
    write_outputs(payload, tmp_path)
    # 严格 JSON:任何裸 NaN 都会让前端 fetch().json() 挂掉
    text = (tmp_path / "latest.json").read_text(encoding="utf-8")
    json.loads(text)
    assert "NaN" not in text
    assert (tmp_path / "index.html").read_text(encoding="utf-8").startswith("<!doctype html>")


def test_payload_with_nan_amount_never_full_score():
    bundle = _bundle()
    bundle.market["amount"] = np.nan  # 成交额整列缺失
    payload = clean_payload(build_payload(bundle))
    market = payload["market"]
    assert market["volume_state"] == "UNKNOWN"
    assert market["predicted_direction"] is None
    # 缺数据日绝不能打出高分结论(修复 min(100, NaN)=100 缺陷)
    assert market["score"]["level"] is None
    assert market["conclusion"] == "量能数据不足，今日不给方向判断"
    json.dumps(payload, allow_nan=False)


def test_no_data_payload_and_markdown():
    empty = DataBundle(pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), "none", "上游全部失败")
    payload = build_payload(empty)
    assert payload["status"] == "no_data"
    md = render_markdown(payload)
    assert "暂无可验证行情数据" in md
    assert "上游全部失败" in md


def test_markdown_tolerates_none_values():
    payload = clean_payload(build_payload(_bundle(with_industries=False)))
    payload["market"]["pct_chg"] = None
    payload["market"]["amount"] = None
    md = render_markdown(payload)  # None 不能让 :.2f 崩溃
    assert "核心结论" in md
    assert "—" in md


def test_markdown_shows_stale_banner():
    payload = clean_payload(build_payload(_bundle()))
    payload["stale"] = True
    payload["warning"] = "本次数据源未更新，沿用 2026-07-24 数据"
    md = render_markdown(payload)
    assert "⚠️" in md
    assert "2026-07-24" in md


def test_html_uses_a_share_colors():
    from investment_dashboard.report import _HTML
    # A股习惯:红涨绿跌。pos 必须映射到红色系变量
    assert "--up-text:#c2414b" in _HTML
    assert ".pos{color:var(--up-text)}" in _HTML
    assert "prefers-color-scheme: dark" in _HTML
