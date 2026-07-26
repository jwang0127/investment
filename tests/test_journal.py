import json

import pandas as pd

from investment_dashboard.journal import (
    append_prediction,
    journal_record,
    load_last_success,
    load_records,
    resolve_predictions,
    save_last_success,
    summarize_journal,
)


def _payload(date="2026-07-24", signal=0.3, state="EXPAND", close=3800.0):
    return {
        "generated_at": "2026-07-24T10:00:00+00:00",
        "status": "ok",
        "market": {
            "date": date, "close": close, "pct_chg": 0.5,
            "volume_state": state, "volume_signal": signal,
            "score": {"total": 66.0},
        },
    }


def test_journal_record_direction_thresholds():
    assert journal_record(_payload(signal=0.11))["predicted_direction"] == "up"
    assert journal_record(_payload(signal=-0.11))["predicted_direction"] == "down"
    assert journal_record(_payload(signal=0.05))["predicted_direction"] == "flat"
    assert journal_record(_payload(signal=0.5, state="UNKNOWN"))["predicted_direction"] is None
    assert journal_record({"market": {}}) is None


def test_append_dedupes_by_date(tmp_path):
    path = tmp_path / "journal.jsonl"
    append_prediction(path, _payload(signal=0.2))
    append_prediction(path, _payload(signal=-0.2))  # 同日重跑，应替换
    append_prediction(path, _payload(date="2026-07-25", signal=0.3))
    rows = load_records(path)
    assert len(rows) == 2
    assert rows[0]["date"] == "2026-07-24"
    assert rows[0]["predicted_direction"] == "down"


def test_append_preserves_resolved_fields(tmp_path):
    path = tmp_path / "journal.jsonl"
    append_prediction(path, _payload(signal=0.2))
    market = pd.DataFrame({"date": ["2026-07-24", "2026-07-25"], "close": [3800.0, 3850.0]})
    assert resolve_predictions(path, market) == 1
    # 同日再次运行不能把已回填的验证结果洗掉
    append_prediction(path, _payload(signal=0.2))
    row = load_records(path)[0]
    assert row["actual_direction"] == "up"
    assert row["hit"] is True


def test_resolve_backfills_and_is_idempotent(tmp_path):
    path = tmp_path / "journal.jsonl"
    append_prediction(path, _payload(date="2026-07-23", signal=-0.2, close=3900.0))
    append_prediction(path, _payload(date="2026-07-24", signal=0.2, close=3800.0))
    market = pd.DataFrame({
        "date": ["2026-07-23", "2026-07-24", "2026-07-25"],
        "close": [3900.0, 3800.0, 3850.0],
    })
    assert resolve_predictions(path, market) == 2
    rows = load_records(path)
    first, second = rows
    assert first["actual_direction"] == "down"  # 3900 -> 3800
    assert first["hit"] is True
    assert first["actual_return_pct"] == round((3800 / 3900 - 1) * 100, 3)
    assert second["actual_direction"] == "up"  # 3800 -> 3850
    assert resolve_predictions(path, market) == 0  # 幂等
    # 最后一天没有下一交易日，保持 pending
    append_prediction(path, _payload(date="2026-07-25", signal=0.2, close=3850.0))
    assert resolve_predictions(path, market) == 0
    assert load_records(path)[-1]["actual_direction"] is None


def test_resolve_handles_legacy_full_payload(tmp_path):
    path = tmp_path / "journal.jsonl"
    legacy = _payload(date="2026-07-23", signal=0.2, close=3800.0)
    path.write_text(json.dumps(legacy, ensure_ascii=False) + "\n", encoding="utf-8")
    market = pd.DataFrame({"date": ["2026-07-23", "2026-07-24"], "close": [3800.0, 3810.0]})
    assert resolve_predictions(path, market) == 1
    row = load_records(path)[0]
    assert row["date"] == "2026-07-23"
    assert row["actual_direction"] == "up"


def test_summarize_reports_baseline_and_edge(tmp_path):
    path = tmp_path / "journal.jsonl"
    rows = []
    # 10 条已验证：7 中 3 错，实际 up 占 6 成
    for i in range(10):
        rows.append({
            "date": f"2026-07-{i + 1:02d}", "predicted_direction": "up" if i < 8 else "down",
            "actual_direction": "up" if i < 6 else "down",
            "hit": (i < 6) or (i >= 8),
        })
    rows.append({"date": "2026-07-20", "predicted_direction": "flat", "actual_direction": "up", "hit": None})
    rows.append({"date": "2026-07-21", "predicted_direction": "up", "actual_direction": None, "hit": None})
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    summary = summarize_journal(path)
    assert summary["observations"] == 10
    assert summary["hit_rate"] == 0.8
    assert summary["baseline_up_rate"] == 0.6
    assert summary["edge"] == round(0.8 - 0.6, 4)
    assert summary["pending"] == 1
    assert summary["coverage"] == round(11 / 12, 4)


def test_last_success_snapshot_roundtrip(tmp_path):
    snapshot = tmp_path / "last_success.json"
    journal = tmp_path / "journal.jsonl"
    assert load_last_success(snapshot, journal) is None
    payload = _payload()
    save_last_success(snapshot, payload)
    assert load_last_success(snapshot, journal)["market"]["date"] == "2026-07-24"
    # 快照损坏时回退到 journal 里的旧版完整 payload
    snapshot.write_text("{broken", encoding="utf-8")
    journal.write_text(json.dumps(_payload(date="2026-07-23")) + "\n", encoding="utf-8")
    assert load_last_success(snapshot, journal)["market"]["date"] == "2026-07-23"
