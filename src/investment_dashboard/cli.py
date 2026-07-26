from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .data_sources import fetch_free_data
from .journal import (
    append_prediction,
    load_last_success,
    resolve_predictions,
    save_last_success,
    summarize_journal,
)
from .news_sources import abstract_news, fetch_news, policy_expectations
from .notifier import send_feishu
from .report import build_payload, clean_payload, write_outputs

_HISTORY_COLUMNS = ["date", "industry", "pct_chg", "amount", "breadth"]
_HISTORY_KEEP_DAYS = 250


def _update_industry_history(path: Path, industries: pd.DataFrame) -> pd.DataFrame:
    """行业快照逐日落盘：单日快照算不出 ret5/ret20，历史攒够后时序因子才生效。"""
    if industries is None or industries.empty:
        return pd.read_csv(path, dtype={"date": str}) if path.exists() else pd.DataFrame()
    snap = industries.copy()
    snap["date"] = snap["date"].astype(str).str.slice(0, 10)
    snap = snap[[c for c in _HISTORY_COLUMNS if c in snap.columns]]
    if path.exists():
        try:
            history = pd.concat([pd.read_csv(path, dtype={"date": str}), snap], ignore_index=True)
        except Exception:
            history = snap
    else:
        history = snap
    history = history.drop_duplicates(["date", "industry"], keep="last")
    keep = sorted(history["date"].unique())[-_HISTORY_KEEP_DAYS:]
    history = history[history["date"].isin(keep)].sort_values(["industry", "date"]).reset_index(drop=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    history.to_csv(path, index=False)
    return history


def _stale_fallback(bundle, data_dir: Path) -> dict | None:
    previous = load_last_success(data_dir / "last_success.json", data_dir / "prediction_journal.jsonl")
    if not previous:
        return None
    previous["status"] = "ok"
    previous["stale"] = True
    previous["generated_at"] = datetime.now(timezone.utc).isoformat()
    previous["warning"] = (
        f"本次数据源未更新，沿用最近已验证交易日 {previous.get('market', {}).get('date')} 数据：{bundle.warning}"
    )
    return previous


def _feishu_text(payload: dict) -> str:
    market = payload.get("market") or {}
    review = payload.get("review") or {}
    direction = {"up": "看多", "down": "看空", "flat": "中性"}.get(market.get("predicted_direction"), "不判断")
    hit = review.get("hit_rate")
    review_line = (
        f"复盘：{review.get('observations', 0)} 样本，胜率 {hit:.0%}" if isinstance(hit, float) else "复盘样本积累中"
    )

    def _v(value, suffix=""):
        return f"{value}{suffix}" if value is not None else "—"

    return (
        f"量潮罗盘｜{_v(market.get('date'))}\n"
        f"{_v(market.get('conclusion'))}\n"
        f"上证 {_v(market.get('close'))}（{_v(market.get('pct_chg'), '%')}）\n"
        f"量能状态:{_v(market.get('volume_state_label') or market.get('volume_state'))}"
        f"｜量能分数:{_v(market.get('volume_signal'))}｜模型方向:{direction}\n"
        f"{review_line}\n数据源:{_v(payload.get('source'))}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="生成量潮罗盘每日大盘看板")
    parser.add_argument("--days", type=int, default=320)
    parser.add_argument("--output", default="public")
    parser.add_argument("--data-dir", default="data")
    args = parser.parse_args()
    data_dir = Path(args.data_dir)
    journal_path = data_dir / "prediction_journal.jsonl"

    bundle = fetch_free_data(args.days)
    as_of = pd.to_datetime(bundle.market["date"]).max().date() if not bundle.market.empty else None
    news_warnings: list[str] = []
    bundle.news = abstract_news(fetch_news(as_of, news_warnings))
    bundle.policy_expectations = policy_expectations(as_of) if as_of else []

    # 先用今天新到的日线回填历史预测的实际方向，再写当天的新预测。
    resolve_predictions(journal_path, None if bundle.market.empty else bundle.market)

    industry_history = _update_industry_history(data_dir / "industry_history.csv", bundle.industries)
    payload = build_payload(bundle, industry_history=industry_history)
    if payload["status"] == "no_data":
        fallback = _stale_fallback(bundle, data_dir)
        if fallback:
            payload = fallback
    if news_warnings:
        payload["warning"] = "；".join(filter(None, [payload.get("warning", ""), *news_warnings]))
    payload = clean_payload(payload)
    if payload["status"] == "ok" and not payload.get("stale"):
        save_last_success(data_dir / "last_success.json", payload)
        append_prediction(journal_path, payload)  # stale 日只读不写，避免旧记录被洗掉
    payload["review"] = summarize_journal(journal_path)
    write_outputs(payload, args.output)
    if payload["status"] == "ok":
        send_feishu(_feishu_text(payload))
    print(f"status={payload['status']} source={payload.get('source')} warning={payload.get('warning', '')} output={args.output}")


if __name__ == "__main__":
    main()
