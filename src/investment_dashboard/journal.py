"""每日预测留痕与复盘闭环。

当天记录 predicted_direction，下一个交易日用真实收盘回填 actual_direction，
让 summarize_journal 的胜率成为可验证的滚动统计，而不是永远为空的占位。

- 日志按交易日去重：同一天重复运行只保留最后一次记录（已回填的字段不丢失）。
- |volume_signal| 低于死区的预测记为 flat，不计入方向胜率但计入覆盖率。
- 胜率必须与“始终看多/看空”基线并列输出，否则没有信息量。
"""

from __future__ import annotations

import json
from pathlib import Path

from .volume_model import SIGNAL_DEAD_ZONE


def load_records(path: str | Path) -> list[dict]:
    target = Path(path)
    if not target.exists():
        return []
    rows = []
    for line in target.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _write_records(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def _record_date(row: dict) -> str | None:
    # 兼容两种格式：新版精简记录的 date，旧版完整 payload 的 market.date。
    return row.get("date") or (row.get("market") or {}).get("date")


def _record_close(row: dict) -> float | None:
    value = row.get("close", (row.get("market") or {}).get("close"))
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None  # NaN 保护


def journal_record(payload: dict) -> dict | None:
    """把完整 payload 压缩成日志行；无有效行情或 UNKNOWN 状态时不给方向。"""
    market = payload.get("market") or {}
    if not market.get("date"):
        return None
    signal = market.get("volume_signal")
    state = market.get("volume_state")
    if signal is None or state in (None, "UNKNOWN"):
        predicted = None
    elif signal > SIGNAL_DEAD_ZONE:
        predicted = "up"
    elif signal < -SIGNAL_DEAD_ZONE:
        predicted = "down"
    else:
        predicted = "flat"
    return {
        "date": market.get("date"),
        "generated_at": payload.get("generated_at"),
        "close": market.get("close"),
        "pct_chg": market.get("pct_chg"),
        "volume_state": state,
        "volume_signal": signal,
        "breadth_pct": market.get("breadth_pct"),
        "score": (market.get("score") or {}).get("total"),
        "predicted_direction": predicted,
        "horizon": 1,  # 与 5 日回测口径区分：闭环验证的是次日方向
        "actual_direction": None,
        "actual_return_pct": None,
        "hit": None,
    }


def append_prediction(path: str | Path, payload: dict) -> None:
    """按交易日 upsert 当日记录；旧版完整 payload 输入会被压缩成精简记录。"""
    record = payload if "predicted_direction" in payload else journal_record(payload)
    if not record or not record.get("date"):
        return
    target = Path(path)
    rows = []
    for row in load_records(target):
        if _record_date(row) != record["date"]:
            rows.append(row)
        elif row.get("actual_direction") is not None and record.get("actual_direction") is None:
            # 同日旧记录已被回填过，保留回填结果。
            for key in ("actual_direction", "actual_return_pct", "hit", "next_close"):
                if key in row:
                    record[key] = row[key]
    rows.append(record)
    rows.sort(key=lambda r: _record_date(r) or "")
    _write_records(target, rows)


def resolve_predictions(path: str | Path, market: "object" = None) -> int:
    """用市场日线（含 date, close 列的 DataFrame）回填 actual_direction。

    返回本次新回填的数量。只回填仍为空、且日线中存在下一交易日收盘的记录。
    """
    target = Path(path)
    rows = load_records(target)
    if not rows or market is None or getattr(market, "empty", True):
        return 0
    import pandas as pd

    frame = market.copy()
    frame["date"] = pd.to_datetime(frame["date"]).dt.strftime("%Y-%m-%d")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.dropna(subset=["close"]).drop_duplicates("date").sort_values("date")
    dates = frame["date"].tolist()
    close_by_date = dict(zip(dates, frame["close"].astype(float)))
    resolved = 0
    for row in rows:
        if row.get("actual_direction") is not None:
            continue
        day = _record_date(row)
        base_close = _record_close(row)
        if day is None or base_close is None:
            continue
        later = [d for d in dates if d > day]
        if not later:
            continue  # 下一交易日还没发生
        next_close = close_by_date.get(later[0])
        if next_close is None:
            continue
        if "date" not in row:  # 旧版完整 payload 原地补充字段，不丢历史
            row["date"] = day
        ret = next_close / base_close - 1
        row["actual_direction"] = "up" if ret > 0 else ("down" if ret < 0 else "flat")
        row["actual_return_pct"] = round(ret * 100, 3)
        row["next_close"] = round(float(next_close), 2)
        predicted = row.get("predicted_direction")
        row["hit"] = (predicted == row["actual_direction"]) if predicted in ("up", "down") else None
        resolved += 1
    if resolved:
        _write_records(target, rows)
    return resolved


def _hit_rate(sample: list[dict]) -> float | None:
    if not sample:
        return None
    return round(sum(1 for r in sample if r.get("hit")) / len(sample), 4)


def summarize_journal(path: str | Path, window: int = 20) -> dict:
    """滚动复盘：胜率 + 基线对照 + 多空拆分；flat/UNKNOWN 不计方向对错。"""
    rows = load_records(path)
    predictions = [r for r in rows if r.get("predicted_direction") in ("up", "down", "flat")]
    checked = [
        r for r in predictions
        if r.get("predicted_direction") in ("up", "down") and r.get("actual_direction") in ("up", "down", "flat")
    ]
    ups = [r for r in checked if r.get("predicted_direction") == "up"]
    downs = [r for r in checked if r.get("predicted_direction") == "down"]
    baseline_up = None
    edge = None
    if checked:
        baseline_up = round(sum(1 for r in checked if r.get("actual_direction") == "up") / len(checked), 4)
        rate = _hit_rate(checked)
        if rate is not None:
            edge = round(rate - max(baseline_up, 1 - baseline_up), 4)
    pending = sum(
        1 for r in predictions
        if r.get("predicted_direction") in ("up", "down") and r.get("actual_direction") is None
    )
    return {
        "observations": len(checked),
        "hit_rate": _hit_rate(checked),
        "recent_observations": len(checked[-window:]),
        "recent_hit_rate": _hit_rate(checked[-window:]),
        "long_hit_rate": _hit_rate(ups),
        "short_hit_rate": _hit_rate(downs),
        "baseline_up_rate": baseline_up,
        "edge": edge,
        "coverage": round(len([r for r in predictions if r["predicted_direction"] != "flat"]) / len(predictions), 4) if predictions else None,
        "pending": pending,
    }


def save_last_success(path: str | Path, payload: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def load_last_success(path: str | Path, journal_path: str | Path | None = None) -> dict | None:
    """优先读独立的 last_success 快照；兼容旧仓库从 journal 里翻完整 payload。"""
    target = Path(path)
    if target.exists():
        try:
            row = json.loads(target.read_text(encoding="utf-8"))
            if row.get("status") == "ok" and row.get("market"):
                return row
        except json.JSONDecodeError:
            pass
    if journal_path is None:
        return None
    for row in reversed(load_records(journal_path)):
        if row.get("status") == "ok" and row.get("market"):
            return row
    return None
