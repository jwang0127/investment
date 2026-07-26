import json
from pathlib import Path

import numpy as np
import pandas as pd

from investment_dashboard import cli
from investment_dashboard.data_sources import DataBundle
from investment_dashboard.journal import load_records


def _bundle(n=80, last_date=None):
    rng = np.random.default_rng(23)
    dates = pd.date_range("2026-03-01", periods=n, freq="B").strftime("%Y-%m-%d")
    market = pd.DataFrame({
        "date": dates,
        "close": 3000 + np.cumsum(rng.normal(0, 15, n)),
        "amount": 1.4e12 + rng.normal(0, 1e11, n),
    })
    market["pct_chg"] = market["close"].pct_change() * 100
    industries = pd.DataFrame({
        "date": dates[-1],
        "industry": [f"行业{i}" for i in range(8)],
        "close": 100.0, "amount": rng.uniform(1e10, 8e10, 8),
        "pct_chg": rng.normal(0, 2, 8), "breadth": rng.uniform(30, 70, 8),
    })
    return DataBundle(
        market, industries, pd.DataFrame(), "test", "",
        indices={}, news=[], breadth={"up": 3000, "down": 2000, "flat": 400, "total": 5400},
    )


def test_industry_history_accumulates_and_dedupes(tmp_path):
    path = tmp_path / "industry_history.csv"
    day1 = pd.DataFrame({"date": "2026-07-24", "industry": ["A", "B"], "pct_chg": [1.0, -1.0], "amount": [1e10, 2e10], "breadth": [60.0, 40.0]})
    cli._update_industry_history(path, day1)
    cli._update_industry_history(path, day1.assign(pct_chg=[1.5, -0.5]))  # 同日重跑覆盖
    day2 = day1.assign(date="2026-07-25")
    history = cli._update_industry_history(path, day2)
    assert len(history) == 4
    assert history[(history["date"] == "2026-07-24") & (history["industry"] == "A")]["pct_chg"].iloc[0] == 1.5
    # 空快照时返回既有历史，不清空文件
    unchanged = cli._update_industry_history(path, pd.DataFrame())
    assert len(unchanged) == 4


def test_main_end_to_end_offline(tmp_path, monkeypatch):
    bundle = _bundle()
    monkeypatch.setattr(cli, "fetch_free_data", lambda days: bundle)
    monkeypatch.setattr(cli, "fetch_news", lambda as_of, warnings=None: [])
    monkeypatch.setattr(cli, "send_feishu", lambda text: False)
    out, data = tmp_path / "public", tmp_path / "data"
    monkeypatch.setattr("sys.argv", ["cli", "--output", str(out), "--data-dir", str(data)])
    cli.main()
    payload = json.loads((out / "latest.json").read_text(encoding="utf-8"))
    assert payload["status"] == "ok"
    assert payload["review"]["observations"] == 0
    rows = load_records(data / "prediction_journal.jsonl")
    assert len(rows) == 1
    assert rows[0]["date"] == bundle.market["date"].iloc[-1]
    assert (data / "last_success.json").exists()
    assert (data / "industry_history.csv").exists()
    assert (out / "report.md").exists() and (out / "index.html").exists()


def test_main_resolves_previous_prediction(tmp_path, monkeypatch):
    out, data = tmp_path / "public", tmp_path / "data"
    bundle = _bundle()
    prev_date = bundle.market["date"].iloc[-2]
    prev_close = float(bundle.market["close"].iloc[-2])
    data.mkdir()
    (data / "prediction_journal.jsonl").write_text(
        json.dumps({
            "date": prev_date, "close": prev_close, "volume_state": "EXPAND",
            "volume_signal": 0.3, "predicted_direction": "up",
            "actual_direction": None, "actual_return_pct": None, "hit": None,
        }) + "\n", encoding="utf-8")
    monkeypatch.setattr(cli, "fetch_free_data", lambda days: bundle)
    monkeypatch.setattr(cli, "fetch_news", lambda as_of, warnings=None: [])
    monkeypatch.setattr(cli, "send_feishu", lambda text: False)
    monkeypatch.setattr("sys.argv", ["cli", "--output", str(out), "--data-dir", str(data)])
    cli.main()
    rows = load_records(data / "prediction_journal.jsonl")
    resolved = [r for r in rows if r["date"] == prev_date][0]
    went_up = float(bundle.market["close"].iloc[-1]) > prev_close
    assert resolved["actual_direction"] == ("up" if went_up else "down")
    assert resolved["hit"] is (resolved["predicted_direction"] == resolved["actual_direction"])
    payload = json.loads((out / "latest.json").read_text(encoding="utf-8"))
    assert payload["review"]["observations"] == 1


def test_main_stale_fallback_keeps_artifacts_consistent(tmp_path, monkeypatch):
    out, data = tmp_path / "public", tmp_path / "data"
    data.mkdir()
    good = _bundle()
    from investment_dashboard.report import build_payload, clean_payload
    previous = clean_payload(build_payload(good))
    (data / "last_success.json").write_text(json.dumps(previous, ensure_ascii=False), encoding="utf-8")
    empty = DataBundle(pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), "none", "上游全挂")
    monkeypatch.setattr(cli, "fetch_free_data", lambda days: empty)
    monkeypatch.setattr(cli, "fetch_news", lambda as_of, warnings=None: [])
    monkeypatch.setattr(cli, "send_feishu", lambda text: False)
    monkeypatch.setattr("sys.argv", ["cli", "--output", str(out), "--data-dir", str(data)])
    cli.main()
    payload = json.loads((out / "latest.json").read_text(encoding="utf-8"))
    assert payload["status"] == "ok" and payload["stale"] is True
    assert "上游全挂" in payload["warning"]
    md = (out / "report.md").read_text(encoding="utf-8")
    assert "⚠️" in md  # stale 时 report.md 与 latest.json 必须一致，不能还是 no_data 文案
    # stale 日不追加 journal
    assert not (data / "prediction_journal.jsonl").exists() or load_records(data / "prediction_journal.jsonl") == []


def test_feishu_text_reads_market_fields():
    payload = {
        "market": {"date": "2026-07-24", "close": 3800.0, "pct_chg": 0.5, "conclusion": "测试结论",
                   "volume_state_label": "温和放量", "volume_signal": 0.3, "predicted_direction": "up"},
        "review": {"observations": 5, "hit_rate": 0.6},
        "source": "eastmoney",
    }
    text = cli._feishu_text(payload)
    assert "2026-07-24" in text and "测试结论" in text and "温和放量" in text
    assert "None" not in text
